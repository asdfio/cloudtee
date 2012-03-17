cloudtee-server
===============

The fabfile is the easiest way to deploy cloudtee-server. Install fabric and
use the following tasks to prepare, install and control cloudtee-server:

* `provision` allows you to boot a server on an OpenStack cloud. Simply 
  define the following OpenStack environment variables and run the task:

    OS_USERNAME
    OS_PASSWORD
    OS_TENANT_NAME
    OS_AUTH_URL

  In addition to defining your OpenStack environment, you can also configure
  how your server is built for CloudTee:

    CT_KEY_NAME=cloudtee

  If a keypair does not exist with the name in CT\_KEY\_NAME, this task will
  attempt to import your public ssh key from ~/.ssh/id\_rsa.pub.

    CT_SEC_GROUP=cloudtee

  A security group will be created with ports 22 and 8080 open with this name.

    CT_IMAGE_NAME=oneiric-server-cloudimg-amd64

  The image used to boot the server must be defined, otherwise the default
  name will be used.
  
    CT_FLAVOR_NAME=m1.large

  Use this to override the flavor used during server creation.

* `deploy` will copy the local source code to a host defined in the CT\_HOST
  environment variable.
* `start` and `stop` also use CT\_HOST to manage the cloudtee-server process
  running on your remote server
