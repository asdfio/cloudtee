import setuptools

setuptools.setup(
    name='cloudtee-server',
    version='0.0.1',
    url='http://cloudtee.me',
    author='Brian Waldon',
    author_email='bcwaldon@gmail.com',
    scripts=['bin/cloudtee-server'],
    install_requires=['eventlet', 'python-novaclient', 'fabric'],
)
