from setuptools import setup, find_packages

with open("requirements.txt") as f:
	install_requires = f.read().strip().split("\n")

# get version from __version__ variable in erpnext_addon/__init__.py
from erpnext_addon import __version__ as version

setup(
	name="erpnext_addon",
	version=version,
	description="ERPnext Addon",
	author="Alvin",
	author_email="zhangliankun0907@foxmail.com",
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
