##Mock test for bare cluster

To run the test you need:

1. `pip install mock`

2. The script needs the auth_url and token from okeanos those are located in a file inside         `<project>/.private/.config.txt `the file has the following format:

    [global]

	default_cloud = ~okeanos

	[cloud "~okeanos"]

	url = https://accounts.okeanos.grnet.gr/identity/v2.0

	token = YOUR TOKEN

	project_id = the e-science project id, needed for the ember_django  application to run

	[cluster]
	
	master_ip = x.x.x.x not needed for test_create_cluster

	[deploy]

	url = your base url (eg for localhost http://127.0.0.1:8000/) not needed for test_create_cluster
	
	[project]
	
	name = the name of the project you want to run


Run mock test with either with `nosetests`, or  with `python test_create_bare_cluster.py`