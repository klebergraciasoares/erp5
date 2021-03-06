##############################################################################
#
# Copyright (c) 2011 Nexedi SA and Contributors. All Rights Reserved.
#
# WARNING: This program as such is intended to be used by professional
# programmers who take the whole responsibility of assessing all potential
# consequences resulting from its eventual inadequacies and bugs
# End users who are looking for a ready-to-use solution with commercial
# guarantees and support are strongly advised to contract a Free Software
# Service Company
#
# This program is Free Software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 3
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
##############################################################################
from datetime import datetime,timedelta
import os
import subprocess
import sys
import time
import glob
import SlapOSControler
import json
import time
import shutil
import logging
import string
import random
from ProcessManager import SubprocessError, ProcessManager, CancellationError
from subprocess import CalledProcessError
from NodeTestSuite import SlapOSInstance
from Updater import Updater
from erp5.util import taskdistribution

class UnitTestRunner():
  def __init__(self, testnode):
    self.testnode = testnode
    self.slapos_controler = SlapOSControler.SlapOSControler(
                                  self.testnode.working_directory,
                                  self.testnode.config,
                                  self.testnode.log)

  def _prepareSlapOS(self, working_directory, slapos_instance, log,
          create_partition=1, software_path_list=None, **kw):
    """
    Launch slapos to build software and partitions
    """
    slapproxy_log = os.path.join(self.testnode.config['log_directory'],
                                  'slapproxy.log')
    log('Configured slapproxy log to %r' % slapproxy_log)
    reset_software = slapos_instance.retry_software_count > 10
    if reset_software:
      slapos_instance.retry_software_count = 0
    log('testnode, retry_software_count : %r' % \
             slapos_instance.retry_software_count)
    self.slapos_controler.initializeSlapOSControler(slapproxy_log=slapproxy_log,
       process_manager=self.testnode.process_manager, reset_software=reset_software,
       software_path_list=software_path_list)
    self.testnode.process_manager.supervisord_pid_file = os.path.join(\
         self.slapos_controler.instance_root, 'var', 'run', 'supervisord.pid')
    method_list= ["runSoftwareRelease"]
    if create_partition:
      method_list.append("runComputerPartition")
    for method_name in method_list:
      slapos_method = getattr(self.slapos_controler, method_name)
      log("Before status_dict = slapos_method(...)")
      status_dict = slapos_method(self.testnode.config,
                                  environment=self.testnode.config['environment'],
                                 )
      log(status_dict)
      log("After status_dict = slapos_method(...)")
      if status_dict['status_code'] != 0:
         slapos_instance.retry = True
         slapos_instance.retry_software_count += 1
         raise SubprocessError(status_dict)
      else:
         slapos_instance.retry_software_count = 0
    return status_dict

  def prepareSlapOSForTestNode(self, test_node_slapos):
    """
    We will build slapos software needed by the testnode itself,
    like the building of selenium-runner by default
    """
    return self._prepareSlapOS(self.testnode.config['slapos_directory'],
              test_node_slapos, self.testnode.log, create_partition=0,
              software_path_list=self.testnode.config.get("software_list"))

  def prepareSlapOSForTestSuite(self, node_test_suite):
    """
    Build softwares needed by testsuites
    """
    log = self.testnode.log
    if log is None:
      log = self.testnode.log
    return self._prepareSlapOS(node_test_suite.working_directory,
              node_test_suite, log,
              software_path_list=[node_test_suite.custom_profile_path])

  def runTestSuite(self, node_test_suite, portal_url, log=None):
    config = self.testnode.config
    parameter_list = []
    run_test_suite_path_list = glob.glob("%s/*/bin/runTestSuite" % \
        self.slapos_controler.instance_root)
    if not len(run_test_suite_path_list):
      raise ValueError('No runTestSuite provided in installed partitions.')
    run_test_suite_path = run_test_suite_path_list[0]
    run_test_suite_revision = node_test_suite.revision
    # Deal with Shebang size limitation
    invocation_list = self.testnode._dealShebang(run_test_suite_path)
    invocation_list.extend([run_test_suite_path,
                           '--test_suite', node_test_suite.test_suite,
                           '--revision', node_test_suite.revision,
                           '--test_suite_title', node_test_suite.test_suite_title,
                           '--node_quantity', config['node_quantity'],
                           '--master_url', portal_url])
    firefox_bin_list = glob.glob("%s/soft/*/parts/firefox/firefox-slapos" % \
        config["slapos_directory"])
    if len(firefox_bin_list):
      parameter_list.append('--firefox_bin')
    xvfb_bin_list = glob.glob("%s/soft/*/parts/xserver/bin/Xvfb" % \
        config["slapos_directory"])
    if len(xvfb_bin_list):
      parameter_list.append('--xvfb_bin')
    supported_paramater_set = self.testnode.process_manager.getSupportedParameterSet(
                           run_test_suite_path, parameter_list)
    if '--firefox_bin' in supported_paramater_set:
      invocation_list.extend(["--firefox_bin", firefox_bin_list[0]])
    if '--xvfb_bin' in supported_paramater_set:
      invocation_list.extend(["--xvfb_bin", xvfb_bin_list[0]])
    # TODO : include testnode correction ( b111682f14890bf )
    if hasattr(node_test_suite,'additional_bt5_repository_id'):
      additional_bt5_path = os.path.join(
              node_test_suite.working_directory,
              node_test_suite.additional_bt5_repository_id)
      invocation_list.extend(["--bt5_path", additional_bt5_path])
    # From this point, test runner becomes responsible for updating test
    # result. We only do cleanup if the test runner itself is not able
    # to run.
    SlapOSControler.createFolder(node_test_suite.test_suite_directory,
                                 clean=True)
    self.testnode.process_manager.spawn(*invocation_list,
                          cwd=node_test_suite.test_suite_directory,
                          log_prefix='runTestSuite', get_output=False)

  def getRelativePathUsage(self):
    """
    Used by the method testnode.constructProfile() to know
    if the software.cfg have to use relative path or not.
    """
    return False
