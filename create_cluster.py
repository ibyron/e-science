import sys
from sys import argv
from os.path import abspath
from base64 import b64encode
from kamaki.clients import ClientError
from kamaki.cli.logger import get_logger, add_file_logger
from logging import DEBUG
from kamaki.clients.astakos import AstakosClient
from datetime import *
from dateutil.tz import *
from os.path import expanduser
import os
#  Define loggers
log = get_logger(__name__)
#add_file_logger('kamaki.clients', DEBUG, '%s.log' % __name__)
#add_file_logger(__name__, DEBUG, '%s.log' % __name__)

#  Create progress bar generator
try:
    from progress.bar import Bar

    def create_pb(msg):
        def generator(n):
            bar=Bar(msg)
            for i in bar.iter(range(int(n))):
                yield
            yield
        return generator
except ImportError:
    log.warning('Suggestion: install python-progress')
    def create_pb(msg):
        return None


#  kamaki.config
#  Identity,Account / Astakos

def test_credentials(auth_url,token):
    print(' Test the credentials')
    try:
        auth = AstakosClient(auth_url, token)
        auth.authenticate()
    except ClientError:
        log.debug('Athentication failed with url %s and token %s' % (
            auth_url,token))
        raise
    print 'Authentication verified'
    return auth


def endpoints_and_user_id(auth):
    print(' Get the endpoints')
    try:
        endpoints = dict(
            astakos=auth.get_service_endpoints('identity')['publicURL'],
            cyclades=auth.get_service_endpoints('compute')['publicURL'],
            pithos=auth.get_service_endpoints('object-store')['publicURL'],
            plankton=auth.get_service_endpoints('image')['publicURL'],
            network=auth.get_service_endpoints('network')['publicURL']
            )
        user_id = auth.user_info['id']
    except ClientError:
        print('Failed to get endpoints & user_id from identity server')
        raise
    return endpoints, user_id


#  Object-store / Pithos+

def init_pithos(endpoint, token, user_id):
    from kamaki.clients.pithos import PithosClient

    print(' Initialize Pithos+ client and set account to user uuid')
    try:
        return PithosClient(endpoint, token, user_id)
    except ClientError:
        log.debug('Failed to initialize a Pithos+ client')
        raise


def upload_image(pithos, container, image_path):

    print(' Create the container "images" and use it')
    try:
        pithos.create_container(container, success=(201, ))
    except ClientError as ce:
        if ce.status in (202, ):
            log.warning('Container %s already exists' % container)
        else:
            log.debug('Failed to create container %s' % container)
            raise
    pithos.container = container

    print(' Upload to "images"')
    with open(abspath(image_path)) as f:
        try:
            pithos.upload_object(
                image_path, f,
                hash_cb=create_pb('  Calculating hashes...'),
                upload_cb=create_pb('  Uploading...'))
        except ClientError:
            log.debug('Failed to upload file %s to container %s' % (
                image_path, container))
            raise

def init_network(endpoint, token):
    from kamaki.clients.network import NetworkClient

    print(' Initialize a network client')
    try:
        return NetworkClient(endpoint, token)
    except ClientError:
        log.debug('Failed to initialize network client')
        raise
    
def init_cyclades_netclient(endpoint, token):    
    from kamaki.clients.cyclades import CycladesNetworkClient
    
    print(' Initialize a cyclades network client')
    try:
        return CycladesNetworkClient(endpoint, token)
    except ClientError:
        log.debug('Failed to initialize cyclades network client')
        raise
    
#  Image / Plankton

def init_plankton(endpoint, token):
    from kamaki.clients.image import ImageClient

    print(' Initialize ImageClient')
    try:
        return ImageClient(endpoint, token)
    except ClientError:
        log.debug('Failed to initialize the Image client')
        raise


def register_image(plankton, name, user_id, container, path, properties):

    image_location = (user_id, container, path)
    print(' Register the image')
    try:
         return plankton.register(name, image_location, properties)
    except ClientError:
        log.debug('Failed to register image %s' % name)
        raise


#  Compute / Cyclades

def init_cyclades(endpoint, token):
    from kamaki.clients.cyclades import CycladesClient

    print(' Initialize a cyclades client')
    try:
        return CycladesClient(endpoint, token)
    except ClientError:
        log.debug('Failed to initialize cyclades client')
        raise


class Cluster(object):

    def __init__(self, cyclades, prefix, flavor_id_master,flavor_id_slave, image_id, size,net_client):
        self.client = cyclades
        self.net_client=net_client
        self.prefix, self.size = prefix, int(size)
        self.flavor_id_master,self.flavor_id_slave, self.image_id = flavor_id_master,flavor_id_slave, image_id,

    def list(self):
        return [s for s in self.client.list_servers(detail=True) if (
            s['name'].startswith(self.prefix))]

    def clean_up(self):
        to_delete = self.list()
        print('  There are %s servers to clean up' % len(to_delete))
        for server in to_delete:
            self.client.delete_server(server['id'])
        for server in to_delete:
            self.client.wait_server(
                server['id'], server['status'],
                wait_cb=create_pb(' Deleting %s...' % server['name']))

    def _personality(self, ssh_keys_path='', pub_keys_path=''):
        personality = []
        if pub_keys_path:
            with open(abspath(pub_keys_path)) as f:
                personality.append(dict(
                    contents=b64encode(f.read()),
                    path='/root/.ssh/authorized_keys',
                    owner='root', group='root', mode=0600))
        if ssh_keys_path or pub_keys_path:
                personality.append(dict(
                    contents=b64encode('StrictHostKeyChecking no'),
                    path='/root/.ssh/config',
                    owner='root', group='root', mode=0600))
        return personality

    def create(self, ssh_k_path='', pub_k_path='', server_log_path=''):
        print('\n Create %s servers prefixed as %s' % (
            self.size, self.prefix))
        servers = []
        empty_ip_list=[]
        date_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      
        server_name = '%s%s%s%s%s' % (date_time,'-',self.prefix,'-',1)
        servers.append(self.client.create_server(
                    server_name, self.flavor_id_master, self.image_id,
                    personality=self._personality(ssh_k_path, pub_k_path)))
        self.client.wait_server(servers[0]['id'], current_status='BUILD', delay=1, max_wait=100)
       
        
        net_name=date_time+'-'+self.prefix
        new_network=self.net_client.create_network('MAC_FILTERED',net_name)
        sub_network=self.net_client.create_subnet(new_network['id'],'192.168.0.0/24',enable_dhcp=True)
        self.net_client.create_port(new_network['id'],servers[0]['id'])
        
        
        
        for i in range(2, self.size+1):
            try:
               
                server_name = '%s%s%s%s%s' % (date_time,'-',self.prefix,'-',i)
                servers.append(self.client.create_server(
                    server_name, self.flavor_id_slave, self.image_id,
                    personality=self._personality(ssh_k_path, pub_k_path),
                    networks=empty_ip_list))
                self.client.wait_server(servers[i-1]['id'], current_status='BUILD', delay=2, max_wait=100)
                self.net_client.create_port(new_network['id'],servers[i-1]['id'])
            except ClientError:
                log.debug('Failed while creating server %s' % server_name)
                raise

        if server_log_path:
            print(' Store passwords in file %s' % server_log_path)
            with open(abspath(server_log_path), 'w+') as f:
                from json import dump
                dump(servers, f, indent=2)

        print(' Wait for %s servers to built' % self.size)
        for server in servers:
            new_status = self.client.wait_server(
                server['id'],
                wait_cb=create_pb(' Creating %s...' % server['name']))
            print(' Status for server %s is %s' % (
                server['name'], new_status or 'not changed yet'))
        return servers


def main(opts):

    print('1.  Credentials  and  Endpoints')  
    USER_HOME=os.path.expanduser('~')         
    pub_keys_path=os.path.join(USER_HOME,".ssh/id_rsa.pub")
    cluster_size,flavor_master,flavor_slave,image_name,auth_url,token,name =  int(argv[1]),argv[2],argv[3],argv[4], argv[6],argv[5],argv[7]
   
    
    auth=test_credentials(auth_url,token)
    endpoints, user_id = endpoints_and_user_id(auth)
    
    plankton = init_plankton(endpoints['plankton'], token)    
    list_current_images=plankton.list_public(True,'default')
    for lst in list_current_images:
      if lst['name']==image_name:
         chosen_image=lst

    print('4.  Create  virtual  cluster')
    cluster_master = Cluster(
        cyclades = init_cyclades(endpoints['cyclades'], token),
        prefix=name,
        flavor_id_master=flavor_master,
        flavor_id_slave=flavor_slave,
        image_id=chosen_image['id'],
        size=cluster_size,net_client=init_cyclades_netclient(endpoints['network'], token))
    

    server = cluster_master.create('',pub_keys_path,'')
    

    #  Group servers
''' cluster_servers = cluster.list()

    active = [s for s in cluster_servers if s['status'] == 'ACTIVE']
    print('%s cluster servers are ACTIVE' % len(active))

    attached = [s for s in cluster_servers if s['attachments']]
    print('%s cluster servers are attached to networks' % len(attached))

    build = [s for s in cluster_servers if s['status'] == 'BUILD']
    print('%s cluster servers are being built' % len(build))

    error = [s for s in cluster_servers if s['status'] in ('ERROR')]
    print('%s cluster servers failed (ERROR satus)' % len(error))'''


if __name__ == '__main__':

    #  Add some interaction candy
    from optparse import OptionParser

    kw = {}
    kw['usage'] = '%prog [options]'
    kw['description'] = '%prog deploys a compute cluster on Synnefo w. kamaki'

    parser = OptionParser(**kw)
    parser.disable_interspersed_args()
    parser.add_option('--name',
                      action='store', type='string', dest='name',
                      help='The name to use for naming cluster nodes',
                      default='cluster')
    parser.add_option('--clustersize',
                      action='store', type='string', dest='clustersize',
                      help='Number of virtual cluster nodes to create ',
                      default=2)
    parser.add_option('--flavor-id',
                      action='store', type='int', dest='flavorid',
                      metavar='FLAVOR ID',
                      help='Choose flavor id for the virtual hardware '
                           'of cluster nodes',
                      default=42)
    parser.add_option('--token',
                      action='store', type='string', dest='token',
                      metavar='token',
                      help='Insert token for authentication',
                      default='37cnXlDd1T4VfIsWRtXhaV-iE_2c2_gmiM40gVk3JSs')
    
    
    parser.add_option('--auth_url',
                      action='store', type='string', dest='auth_url',
                      metavar='FLAVOR ID',
                      help='Insert authentication url',
                      default='https://accounts.okeanos.grnet.gr/identity/v2.0')
  
   
   
    ''' parser.add_option('--image-file',
                      action='store', type='string', dest='imagefile',
                      metavar='IMAGE FILE PATH',
                      help='The image file to upload and register ',
                      default='my_image.diskdump')
    parser.add_option('--delete-stale',
                      action='store_true', dest='delete_stale',
                      help='Delete stale servers from previous runs, whose '
                           'name starts with the specified prefix, see '
                           '--prefix',
                      default=False)
    parser.add_option('--container',
                      action='store', type='string', dest='container',
                      metavar='PITHOS+ CONTAINER',
                      help='The Pithos+ container to store image file',
                      default='images')
    parser.add_option('--ssh-key-path',
                      action='store', type='string', dest='sshkeypath',
                      metavar='PATH OF SSH KEYS',
                      help='The ssh keys to inject to server (e.g., id_rsa) ',
                      default='')
    parser.add_option('--pub-key-path',
                      action='store', type='string', dest='pubkeypath',
                      metavar='PATH OF PUBLIC KEYS',
                      help='The public keys to inject to server',
                      default='')
    parser.add_option('--server-log-path',
                      action='store', type='string', dest='serverlogpath',
                      metavar='FILE TO LOG THE VIRTUAL SERVERS',
                      help='Where to store information on created servers '
                           'including superuser passwords',
                      default='')
    parser.add_option('--image-osfamily',
                      action='store', type='string', dest='osfamily',
                      metavar='OS FAMILY',
                      help='linux, windows, etc.',
                      default='linux')
    parser.add_option('--image-root-partition',
                      action='store', type='string', dest='rootpartition',
                      metavar='IMAGE ROOT PARTITION',
                      help='The partition where the root home is ',
                      default='1')'''

    opts, args = parser.parse_args(argv[1:])

    main(opts)

