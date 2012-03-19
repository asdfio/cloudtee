import os
import sys

import fabric.api
import novaclient.exceptions
import novaclient.client


fabric.api.env.user = os.environ.get('CT_USER', 'ubuntu')
fabric.api.env.hosts = [os.environ.get('CT_HOST')]


app_name = 'cloudtee'
subdomain = 'cloudtee'
domain = os.environ.get('DOMAIN')

sec_group_name = os.environ.get('CT_SEC_GROUP_NAME', 'cloudtee')


def dnsimple_req(method, path, body=None):
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


def nova_client():
    """create a new nova client"""
    user = os.environ.get('OS_USERNAME')
    password = os.environ.get('OS_PASSWORD')
    tenant = os.environ.get('OS_TENANT_NAME')
    auth_url = os.environ.get('OS_AUTH_URL')
    client = novaclient.client.Client('2', user, password, tenant, auth_url)
    # FIXME(ja): why do I have to do this? (otherwise service_type is None)
    client.client.service_type = 'compute'
    return client


def record_for_subdomain(subdomain, record_type='A'):
    """Gets the record for a given subdomain or None"""

    records = dnsimple_req('GET', 'records.json')

    for info in records:
        if (info['record']['name'] == subdomain and
            info['record']['record_type'] == record_type):
            return info['record']


def dns(ip, subdomain, record_type='A', ttl=300):
    """creates or updates a subdomain record for a domain"""

    record = record_for_subdomain(subdomain, record_type)

    if record:
        if ip != record['content']:
            body = {
                'record': {
                    'content': ip,
                }
            }
            dnsimple_req('PUT', 'records/%s.json' % record['id'], body)
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
        dnsimple_req('POST', 'records.json', body)
        print 'DNS: %s -> %s [created]' % (subdomain, ip)


def cloud_ip():
    """Ensure we have an IP in DNS and in the cloud.

    if DNS has an IP, make sure our cloud has it.  Otherwise
    allocate a new IP and update DNS.
    """
    record = record_for_subdomain(subdomain)

    client = nova_client()
    floating_ips = client.floating_ips.list()

    if record:
        for fip in floating_ips:
            if fip.ip == record['content']:
                print 'Cloud IP: %s [exists]' % fip.ip
                return True
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
    client = nova_client()
    try:
        sec_group = client.security_groups.find(name=sec_group_name)
        print "Cloud Ports: [exists]"
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


def provision():
    client = nova_client()

    image_name = os.environ.get('CT_IMAGE_NAME',
                                'oneiric-server-cloudimg-amd64')
    image = client.images.find(name=image_name)
    flavor_name = os.environ.get('CT_FLAVOR_NAME', 'm1.large')
    flavor = client.flavors.find(name=flavor_name)

    cloud_ports()

    userdata = """#!/bin/sh

curl https://raw.github.com/asdfio/ssh/master/authorized_keys > ~/.ssh/authorized_keys
sudo apt-get update
sudo apt-get install -y python-pip python-eventlet mongodb python-pymongo
sudo service mongodb start"""

    server = client.servers.create(app_name,
                                   image,
                                   flavor,
                                   userdata=userdata,
                                   security_groups=[sec_group_name])

    # Wait for instance to get fixed ip
    for i in xrange(60):
        server = client.servers.get(server.id)
        if len(server.networks):
            break
        if i == 59:
            print 'Could not get fixed ip. Exiting...'
            sys.exit(1)

    fip = cloud_ip()
    # FIXME(ja): add_floating_ip fails if ip already points at another server
    server.add_floating_ip(fip)
    print 'Success! Instance running at %s' % fip.ip


def deploy():
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
    fabric.api.run('nohup bash -c "cloudtee-server --persistent-topics &"')


def stop():
    fabric.api.run('killall cloudtee-server')


def status():
    print 'APP:', app_name
    print 'DNS:', '%s.%s' % (subdomain, domain)

    record = record_for_subdomain(subdomain)
    if record:
        ip = record['content']
    else:
        ip = None
    print 'IP:', ip
