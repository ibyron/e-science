#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''
This script initialize okeanos utils.

@author: Ioannis Stenos, Nick Vrionis
'''

import os
import sys
import logging
from base64 import b64encode
from os.path import abspath, dirname, join
from datetime import datetime
from kamaki.clients import ClientError
from kamaki.clients.image import ImageClient
from kamaki.clients.astakos import AstakosClient
from kamaki.clients.cyclades import CycladesClient
from kamaki.clients.cyclades import CycladesNetworkClient
from time import sleep
from cluster_errors_constants import *

# Global constants
MAX_WAIT = 300  # Max number of seconds for wait function of Cyclades
BASE_DIR = dirname(abspath(__file__))


def get_project_id(token, project_name="escience.grnet.gr"):
    """
    Return the id of an ~okeanos project.
    """
    auth = check_credentials(token)
    try:
        list_of_projects = auth.get_projects()
    except Exception:
        logging.error('Could not get list of projects')
        sys.exit(error_get_list_projects)
    for project in list_of_projects:
        if project['name'] == project_name:
            return project['id']
    logging.error('No project_id was found for this project_name')
    sys.exit(error_proj_id)


def destroy_cluster(token, master_ip):
    """
    Destroys cluster and deletes network and floating ip. Finds the machines
    that belong to the cluster from the master_ip that is given.
    """
    servers_to_delete = []
    float_ip_to_delete = master_ip
    list_of_errors = []
    master_id = None
    network_to_delete_id = None
    float_ip_to_delete_id = None
    auth = check_credentials(token)
    endpoints, user_id = endpoints_and_user_id(auth)
    cyclades = init_cyclades(endpoints['cyclades'], token)
    nc = init_cyclades_netclient(endpoints['network'], token)
    # Get list of servers and public ips
    try:
        list_of_servers = cyclades.list_servers(detail=True)
        list_of_ips = nc.list_floatingips()
    except Exception:
        logging.error('Could not get list of resources.'
                      'Cannot delete cluster')
        sys.exit(error_get_list_servers)
    logging.log(SUMMARY, ' Starting deletion of requested cluster')
    # Get master virtual machine and network from ip
    for ip in list_of_ips:
        if ip['floating_ip_address'] == float_ip_to_delete:
            master_id = ip['instance_id']
            float_ip_to_delete_id = ip['id']
            master_server = cyclades.get_server_details(master_id)
            for attachment in master_server['attachments']:
                if (attachment['OS-EXT-IPS:type'] == 'fixed' and
                        not attachment['ipv6']):
                    network_to_delete_id = attachment['network_id']
                    break
            break
    # Show an error message and exit if not valid ip or network
    if not master_id:
        logging.error('[%s] is not the valid public ip of the master'
                      % float_ip_to_delete)
        sys.exit(error_get_ip)

    if not network_to_delete_id:
        logging.error('A valid network of master and slaves was not found.'
                      'Deleting the master vm only')
        cyclades.delete_server(master_id)
        sys.exit(error_cluster_corrupt)

    # Get the servers of the cluster to be deleted
    for server in list_of_servers:
        for attachment in server['attachments']:
            if attachment['network_id'] == network_to_delete_id:
                servers_to_delete.append(server)
                break

    number_of_nodes = len(servers_to_delete)

    # Start cluster deleting
    try:
        for server in servers_to_delete:
            cyclades.delete_server(server['id'])
        logging.log(REPORT, ' There are %d servers to clean up'
                    % number_of_nodes)
        # Wait for every server of the cluster to be deleted
        for server in servers_to_delete:
            new_status = cyclades.wait_server(server['id'],
                                              current_status='ACTIVE',
                                              max_wait=MAX_WAIT)
            logging.log(REPORT, ' Server [%s] is being %s', server['name'],
                        new_status)
            if new_status != 'DELETED':
                logging.error('Error deleting server [%s]' % server['name'])
                list_of_errors.append(error_cluster_corrupt)

        logging.log(REPORT, ' Cluster with master node [%s] is %s',
                    servers_to_delete[0]['name'], new_status)
    except Exception:
        logging.exception('Error in deleting server')
        list_of_errors.append(error_cluster_corrupt)

    try:
        nc.delete_network(network_to_delete_id)
        sleep(10)  # Take some time to ensure it is deleted
        logging.log(SUMMARY, ' Network with id [%s] is deleted'
                    % network_to_delete_id)
    except Exception:
        logging.exception('Error in deleting network')
        list_of_errors.append(error_cluster_corrupt)

    # Delete the floating ip of deleted cluster
    try:
        nc.delete_floatingip(float_ip_to_delete_id)
        logging.log(SUMMARY, ' Floating ip [%s] is deleted'
                    % float_ip_to_delete)
    except Exception:
        logging.exception('Error in deleting floating ip [%s]' %
                          float_ip_to_delete)
        list_of_errors.append(error_cluster_corrupt)

    logging.log(SUMMARY, ' Cluster with master node [%s] and %d slave nodes'
                ' was deleted', servers_to_delete[0]['name'],
                number_of_nodes-1)
    # Everything deleted as expected
    if not list_of_errors:
        return 0
    # There was one or more errors, return error message
    else:
        sys.exit(list_of_errors[0])


def check_credentials(token, auth_url='https://accounts.okeanos.grnet.gr'
                      '/identity/v2.0'):
    """Identity,Account/Astakos. Test authentication credentials"""
    logging.log(REPORT, ' Test the credentials')
    try:
        auth = AstakosClient(auth_url, token)
        auth.authenticate()
    except ClientError:
        logging.error('Authentication failed with url %s and token %s' % (
                      auth_url, token))
        sys.exit(error_authentication)
    return auth


def get_flavor_id(token):
    """From kamaki flavor list get all possible flavors """
    auth = check_credentials(token)
    endpoints, user_id = endpoints_and_user_id(auth)
    cyclades = init_cyclades(endpoints['cyclades'], token)
    try:
        flavor_list = cyclades.list_flavors(True)
    except Exception:
        logging.exception('Could not get list of flavors')
        sys.exit(error_flavor_list)
    cpu_list = []
    ram_list = []
    disk_list = []
    disk_template_list = []

    for flavor in flavor_list:
        if flavor['vcpus'] not in cpu_list:
            cpu_list.append(flavor['vcpus'])
        if flavor['ram'] not in ram_list:
            ram_list.append(flavor['ram'])
        if flavor['disk'] not in disk_list:
            disk_list.append(flavor['disk'])
        if flavor['SNF:disk_template'] not in disk_template_list:
            disk_template_list.append(flavor['SNF:disk_template'])
    cpu_list = sorted(cpu_list)
    ram_list = sorted(ram_list)
    disk_list = sorted(disk_list)
    flavors = {'cpus': cpu_list, 'ram': ram_list,
               'disk': disk_list, 'disk_template': disk_template_list}
    return flavors


def check_quota(token, project_id):
    '''
    Checks if user available quota .
    Available = limit minus (used and pending).Also divides with 1024*1024*1024
    to transform bytes to gigabytes.
     '''
    try:
        auth = check_credentials(token)
        dict_quotas = auth.get_quotas()
    except Exception:
        logging.exception('Could not get user quota')
        sys.exit(error_user_quota)

    limit_cd = dict_quotas[project_id]['cyclades.disk']['limit'] / Bytes_to_GB
    usage_cd = dict_quotas[project_id]['cyclades.disk']['usage'] / Bytes_to_GB
    pending_cd = dict_quotas[project_id]['cyclades.disk']['pending'] / Bytes_to_GB
    available_cyclades_disk_GB = (limit_cd-usage_cd-pending_cd)

    limit_cpu = dict_quotas[project_id]['cyclades.cpu']['limit']
    usage_cpu = dict_quotas[project_id]['cyclades.cpu']['usage']
    pending_cpu = dict_quotas[project_id]['cyclades.cpu']['pending']
    available_cpu = limit_cpu - usage_cpu - pending_cpu

    limit_ram = dict_quotas[project_id]['cyclades.ram']['limit'] / Bytes_to_MB
    usage_ram = dict_quotas[project_id]['cyclades.ram']['usage'] / Bytes_to_MB
    pending_ram = dict_quotas[project_id]['cyclades.ram']['pending'] / Bytes_to_MB
    available_ram = (limit_ram-usage_ram-pending_ram)

    limit_vm = dict_quotas[project_id]['cyclades.vm']['limit']
    usage_vm = dict_quotas[project_id]['cyclades.vm']['usage']
    pending_vm = dict_quotas[project_id]['cyclades.vm']['pending']
    available_vm = limit_vm-usage_vm-pending_vm

    quotas = {'cpus': {'limit': limit_cpu, 'available': available_cpu},
              'ram': {'limit': limit_ram, 'available': available_ram},
              'disk': {'limit': limit_cd,
                       'available': available_cyclades_disk_GB},
              'cluster_size': {'limit': limit_vm, 'available': available_vm}}
    return quotas


def endpoints_and_user_id(auth):
    """
    Get the endpoints
    Identity, Account --> astakos
    Compute --> cyclades
    Object-store --> pithos
    Image --> plankton
    Network --> network
    """
    logging.log(REPORT, ' Get the endpoints')
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
        logging.error('Failed to get endpoints & user_id from identity server')
        raise
    return endpoints, user_id


def init_cyclades_netclient(endpoint, token):
    """
    Initialize CycladesNetworkClient
    Cyclades Network client needed for all network functions
    e.g. create network,create floating ip
    """
    logging.log(REPORT, ' Initialize a cyclades network client')
    try:
        return CycladesNetworkClient(endpoint, token)
    except ClientError:
        logging.error('Failed to initialize cyclades network client')
        raise


def init_plankton(endpoint, token):
    """
    Plankton/Initialize Imageclient.
    ImageClient has all registered images.
    """
    logging.log(REPORT, ' Initialize ImageClient')
    try:
        return ImageClient(endpoint, token)
    except ClientError:
        logging.error('Failed to initialize the Image client')
        raise


def init_cyclades(endpoint, token):
    """
    Compute / Initialize Cyclades client.CycladesClient is used
    to create virtual machines
    """
    logging.log(REPORT, ' Initialize a cyclades client')
    try:
        return CycladesClient(endpoint, token)
    except ClientError:
        logging.error('Failed to initialize cyclades client')
        raise


class Cluster(object):
    """
    Cluster class represents an entire ~okeanos cluster.Instantiation of
    cluster gets the following arguments: A CycladesClient object,a name-prefix
    for the cluster,the flavors of master and slave machines,the image id of
    their OS, the size of the cluster,a CycladesNetworkClient object, an
    AstakosClient object and the project_id.
    """
    def __init__(self, cyclades, prefix, flavor_id_master, flavor_id_slave,
                 image_id, size, net_client, auth_cl, project_id):
        self.client = cyclades
        self.nc = net_client
        self.prefix, self.size = prefix, int(size)
        self.flavor_id_master, self.auth = flavor_id_master, auth_cl
        self.flavor_id_slave, self.image_id = flavor_id_slave, image_id
        self.project_id = project_id

    def get_flo_net_id(self):
        """
        Gets an Ipv4 floating network id from the list of public networks Ipv4
        """
        pub_net_list = self.nc.list_networks()
        float_net_id = 1
        i = 1
        for lst in pub_net_list:
            if(lst['status'] == 'ACTIVE' and
               lst['name'] == 'Public IPv4 Network'):
                float_net_id = lst['id']
                try:
                    self.nc.create_floatingip(float_net_id, project_id=self.project_id)
                    return 0
                except ClientError:
                    if i < len(pub_net_list):
                        i = i+1

        return error_get_ip

    def _personality(self, ssh_keys_path='', pub_keys_path=''):
        """Personality injects ssh keys to the virtual machines we create"""
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

    def clean_up(self, servers=None, network=None):
        """Delete resources after a failed attempt to create a cluster"""
        if not (network and servers):
            logging.error('Nothing to delete')
            return
        logging.error(' Cleaning up and shutting down')
        status = ''
        if servers:
            for server in servers:
                status = self.client.get_server_details(server['id'])['status']

                if status == 'BUILD':
                    status = self.client.wait_server(server['id'],
                                                         max_wait=MAX_WAIT)
                self.client.delete_server(server['id'])

                new_status = self.client.wait_server(server['id'],
                                                     current_status=status,
                                                     max_wait=MAX_WAIT)
                logging.log(REPORT, ' Server [%s] is being %s', server['name'],
                            new_status)
                if new_status != 'DELETED':
                    logging.error('Error deleting server [%s]' % server['name'])
        if network:
            self.nc.delete_network(network['id'])

    def create(self, ssh_k_path='', pub_k_path='', server_log_path=''):
        """
        Creates a cluster of virtual machines using the Create_server method of
        CycladesClient.
        """
        logging.log(REPORT, ' Create %s servers prefixed as [%s]',
                    self.size, self.prefix)
        servers = []
        empty_ip_list = []
        list_of_ports = []
        date_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        count = 0
        HOSTNAME_MASTER = ''
        port_status = ''
        # Names the master machine with a timestamp and a prefix name
        # plus number 1
        server_name = '%s%s%s%s%s' % (date_time, '-', self.prefix, '-', 1)
        # Name of the network we will request to create
        net_name = date_time + '-' + self.prefix
        # Creates network
        try:
            new_network = self.nc.create_network('MAC_FILTERED', net_name,
                                                 project_id=self.project_id)
        except Exception:
            logging.exception('Error in creating network')
            sys.exit(error_create_network)
        # Gets list of floating ips
        try:
            list_float_ips = self.nc.list_floatingips()
        except Exception:
            logging.exception('Error getting list of floating ips')
            self.clean_up(network=new_network)
            sys.exit(error_get_ip)
        # If there are existing floating ips,we check if there is any free or
        # if all of them are attached to a machine
        if len(list_float_ips) != 0:
            for float_ip in list_float_ips:
                if float_ip['instance_id'] is None and float_ip['port_id'] is None:
                    break
                else:
                    count = count+1
                    if count == len(list_float_ips):
                        try:
                            self.nc.create_floatingip(list_float_ips
                                                      [count-1]
                                                      ['floating_network_id'],
                                                      project_id=self.project_id)
                        except ClientError:
                            if self.get_flo_net_id() != 0:
                                self.clean_up(network=new_network)
                                logging.error('Error in creating float ip')
                                sys.exit(error_get_ip)
        else:
            # No existing ips,so we create a new one
            # with the floating  network id
            if self.get_flo_net_id() != 0:
                logging.error('Error in creating float ip')
                self.clean_up(network=new_network)
                sys.exit(error_get_ip)
        logging.log(REPORT, ' Wait for %s servers to build', self.size)

        # Creation of master server

        try:
            servers.append(self.client.create_server(
                server_name, self.flavor_id_master, self.image_id,
                personality=self._personality(ssh_k_path, pub_k_path),
                project_id=self.project_id))
        except Exception:
            logging.exception('Error creating master virtual machine [%s]'
                              % servers[0]['name'])
            self.clean_up(servers=servers, network=new_network)
            sys.exit(error_create_server)
        # Creation of slave servers
        for i in range(2, self.size+1):
            try:

                server_name = '%s%s%s%s%s' % (date_time,
                                              '-', self.prefix, '-', i)
                servers.append(self.client.create_server(
                    server_name, self.flavor_id_slave, self.image_id,
                    personality=self._personality(ssh_k_path, pub_k_path),
                    networks=empty_ip_list, project_id=self.project_id))

            except Exception:
                logging.exception('Error creating server [%s]' % server_name)
                self.clean_up(servers=servers, network=new_network)
                sys.exit(error_create_server)
        # We put a wait server for the master here,so we can use the
        # server id later and the slave start their building without
        # waiting for the master to finish building
        try:
            new_status = self.client.wait_server(servers[0]['id'],
                                                 max_wait=MAX_WAIT)
            if new_status != 'ACTIVE':
                logging.error(' Status for server [%s] is %s',
                              servers[i]['name'], new_status)
                self.clean_up(servers=servers, network=new_network)
                sys.exit(error_create_server)
            logging.log(REPORT, ' Status for server [%s] is %s',
                        servers[0]['name'], new_status)
            # Create a subnet for the virtual network between master
            #  and slaves along with the ports needed
            self.nc.create_subnet(new_network['id'], '192.168.0.0/24',
                                  enable_dhcp=True)
            port_details = self.nc.create_port(new_network['id'],
                                               servers[0]['id'])
            port_status = self.nc.wait_port(port_details['id'],
                                            max_wait=MAX_WAIT)
            if port_status != 'ACTIVE':
                logging.error(' Status for port [%s] is %s',
                              port_details['id'], port_status)
                self.clean_up(servers=servers, network=new_network)
                sys.exit(error_create_server)
            # Wait server for the slaves, so we can use their server id
            # in port creation
            for i in range(1, self.size):
                new_status = self.client.wait_server(servers[i]['id'],
                                                     max_wait=MAX_WAIT)
                if new_status != 'ACTIVE':
                    logging.error(' Status for server [%s] is %s',
                                  servers[i]['name'], new_status)
                    self.clean_up(servers=servers, network=new_network)
                    sys.exit(error_create_server)
                logging.log(REPORT, ' Status for server [%s] is %s',
                            servers[i]['name'], new_status)
                port_details = self.nc.create_port(new_network['id'],
                                                   servers[i]['id'])
                list_of_ports.append(port_details)

            for port in list_of_ports:
                port_status = self.nc.get_port_details(port['id'])['status']
                if port_status == 'BUILD':
                    port_status = self.nc.wait_port(port['id'],
                                               max_wait=MAX_WAIT)
                if port_status != 'ACTIVE':
                    logging.error(' Status for port [%s] is %s',
                                  port['id'], port_status)
                    self.clean_up(servers=servers, network=new_network)
                    sys.exit(error_create_server)
        except Exception:
            logging.exception('Error in finalizing cluster creation')
            self.clean_up(servers=servers, network=new_network)
            sys.exit(error_create_server)

        if server_log_path:
            logging.info(' Store passwords in file [%s]', server_log_path)
            with open(abspath(server_log_path), 'w+') as f:
                from json import dump
                dump(servers, f, indent=2)

        # HOSTNAME_MASTER is always the public ip of master node
        master_details = self.client.get_server_details(servers[0]['id'])
        for attachment in master_details['attachments']:
            if attachment['OS-EXT-IPS:type'] == 'floating':
                        HOSTNAME_MASTER = attachment['ipv4']

        return HOSTNAME_MASTER, servers
