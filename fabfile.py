import os
import sys

import fabric.api
import novaclient.exceptions
import novaclient.client


fabric.api.env.user = os.environ.get('CT_USER', 'ubuntu')
fabric.api.env.hosts = [os.environ.get('CT_HOST')]

base_userdata = """#!/bin/sh

curl https://raw.github.com/asdfio/ssh/master/authorized_keys > ~/.ssh/authorized_keys
sudo apt-get update
%s
"""

app_name = 'cloudtee'
subdomain = 'cloudtee'
domain = os.environ.get('DOMAIN')

sec_group_name = os.environ.get('CT_SEC_GROUP_NAME', 'cloudtee')


def _dnsimple_req(method, path, body=None):
    import httplib
    import json
    auth = os.environ.get('DNSIMPLE_AUTH')

    kwargs = {
        'headers': {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-DNSimple-Token': auth,
        },
    }

    if body:
        kwargs['body'] = json.dumps(body)

    conn = httplib.HTTPSConnection('dnsimple.com', '443')
    base_path = '/domains/%s' % domain
    conn.request(method, '%s/%s' % (base_path, path), **kwargs)
    response = conn.getresponse()
    return json.loads(response.read())


def _nova_client():
    """create a new nova client"""
    user = os.environ.get('OS_USERNAME')
    password = os.environ.get('OS_PASSWORD')
    tenant = os.environ.get('OS_TENANT_NAME')
    auth_url = os.environ.get('OS_AUTH_URL')
    client = novaclient.client.Client('2', user, password, tenant, auth_url)
    # FIXME(ja): why do I have to do this? (otherwise service_type is None)
    client.client.service_type = 'compute'
    return client


def _record_for_subdomain(subdomain, record_type='A'):
    """Gets the record for a given subdomain or None"""

    records = _dnsimple_req('GET', 'records.json')

    for info in records:
        if (info['record']['name'] == subdomain and
            info['record']['record_type'] == record_type):
            return info['record']


def dns(ip, subdomain, record_type='A', ttl=300):
    """creates or updates a subdomain record for a domain"""

    record = _record_for_subdomain(subdomain, record_type)

    if record:
        if ip != record['content']:
            body = {
                'record': {
                    'content': ip,
                }
            }
            _dnsimple_req('PUT', 'records/%s.json' % record['id'], body)
            print 'DNS: %s -> %s [updated; was %s]' % (subdomain,
                                                       ip,
                                                       record['content'])
        else:
            print 'DNS: %s -> %s [noop]' % (subdomain, ip)
    else:
        body = {
            'record': {
                'name': subdomain,
                'ttl': ttl,
                'content': ip,
                'record_type': record_type,
            }
        }
        _dnsimple_req('POST', 'records.json', body)
        print 'DNS: %s -> %s [created]' % (subdomain, ip)


def cloud_ip():
    """Ensure we have an IP in DNS and in the cloud.

    if DNS has an IP, make sure our cloud has it.  Otherwise
    allocate a new IP and update DNS.
    """
    record = _record_for_subdomain(subdomain)

    client = _nova_client()
    floating_ips = client.floating_ips.list()

    if record:
        for fip in floating_ips:
            if fip.ip == record['content']:
                print 'Cloud IP: %s [exists]' % fip.ip
                return fip
        print 'Cloud IP: %s [Not found]' % record['content']

    # create/use a new ip
    # FIXME(ja): this logic doesn't work... we should probably find all
    # subdomains in our domain that use the allocated IP and unset them.
    # eg, how to deal with a complete reboot of the cloud...
    fip = client.floating_ips.create()
    print 'Cloud IP: %s [allocated]' % fip.ip
    dns(fip.ip, subdomain)
    return fip


def cloud_ports():
    """ensure ports are open to cloud instances"""
    client = _nova_client()
    try:
        sec_group = client.security_groups.find(name=sec_group_name)
        print "Cloud Ports: %s [exists]" % sec_group_name
    except novaclient.exceptions.NotFound:
        sec_group = client.security_groups.create(sec_group_name,
                                                  sec_group_name)
        pg_id = sec_group.id
        # for pinging
        client.security_group_rules.create(pg_id, 'icmp', -1, -1,
                                           '0.0.0.0/0')
        client.security_group_rules.create(pg_id, 'tcp', 22, 22,
                                           '0.0.0.0/0')
        client.security_group_rules.create(pg_id, 'tcp', 8080, 8080,
                                           '0.0.0.0/0')
        print 'Cloud Ports: %s [created]' % sec_group_name


def _get_server():
    client = _nova_client()
    try:
        server = client.servers.find(name=app_name)
        return server
    except novaclient.exceptions.NotFound:
        pass


def cloud_server():
    """launch a server within the proper security context"""
    client = _nova_client()

    server = _get_server()
    if server:
        print "Server: %s [exists]" % server.id
        return server

    image_name = os.environ.get('CT_IMAGE_NAME',
                                'oneiric-server-cloudimg-amd64')
    image = client.images.find(name=image_name)
    flavor_name = os.environ.get('CT_FLAVOR_NAME', 'm1.large')
    flavor = client.flavors.find(name=flavor_name)

    cmds = """sudo apt-get install -y python-pip python-eventlet mongodb python-pymongo
sudo service mongodb start"""

    server = client.servers.create(app_name,
                                   image,
                                   flavor,
                                   userdata=base_userdata % cmds,
                                   security_groups=[sec_group_name])

    print "Server: %s [created]" % server.id

    # Wait for instance to get fixed ip
    for i in xrange(60):
        server = client.servers.get(server.id)
        if len(server.networks):
            return server
        if i == 59:
            print 'Could not get fixed ip. Exiting...'
            sys.exit(1)


def destroy():
    """destroy the cloud server"""
    server = _get_server()
    if server:
        server.delete()
        print "Server: %s [deleting]" % server.id
    else:
        print "Server: %s [not found]" % app_name


def up():
    """create the cloud environment"""
    cloud_ports()
    server = cloud_server()
    fip = cloud_ip()
    # FIXME(ja): add_floating_ip fails if ip already points at another server
    if fip.instance_id != server.id:
        server.add_floating_ip(fip)
        print "Cloud IP: %s -> %s [associated]" % (fip.ip, server.id)
    else:
        print "Cloud IP: %s -> %s [bound]" % (fip.ip, server.id)


def provision():
    """deploy the application"""
    fabric.api.local('python setup.py sdist --formats=gztar', capture=False)
    pkg_name = fabric.api.local('python setup.py --fullname', capture=True)

    # upload
    fabric.api.put('dist/%s.tar.gz' % pkg_name, '/tmp/')

    # create environment
    fabric.api.run('mkdir /tmp/cloudtee')

    # unzip and install
    with fabric.api.cd('/tmp/cloudtee'):
        fabric.api.run('tar xzf /tmp/%s.tar.gz' % pkg_name)

    with fabric.api.cd('/tmp/cloudtee/%s' % pkg_name):
        fabric.api.run('sudo python setup.py install')

    # cleanup
    fabric.api.run('sudo rm -rf /tmp/cloudtee')


def start():
    """start the application on the server"""
    fabric.api.run('nohup bash -c "cloudtee-server --persistent-topics &"')


def stop():
    """stop the application on the server"""
    fabric.api.run('killall cloudtee-server')


def status():
    """status of the cloud environment [and application]"""
    print 'APP:', app_name
    print 'DNS:', '%s.%s' % (subdomain, domain)

    record = _record_for_subdomain(subdomain)
    if record:
        print "IP:", record['content']
    else:
        print "IP:", None

    server = _get_server()
    if server:
        print "Server:", server.id
    else:
        print "Server:", None
