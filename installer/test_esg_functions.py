#!/usr/bin/local/env python

import unittest
import esg_functions
import os


class test_ESG_Functions(unittest.TestCase):

    # @classmethod
    # def setUpClass(self):
        # try: 
        #     os.makedirs("/esg/esgf-install-manifest")
        # except OSError:
        #     if not os.path.isdir("/esg/esgf-install-manifest"):
        #         raise

    # def setUp(self):
    #     if os.path.isdir("/home/el"):
    #         print "found path"
    #     else:
    #         print "did not find path"

    def test_version_comp(self):
        output = esg_functions.version_comp("2:2.3.4-5", "3:2.5.3-1")

        self.assertEqual(output, -1)

        output = esg_functions.version_comp("3:2.5.3-1", "2:2.3.4-5")
        self.assertEqual(output, 1)

        output = esg_functions.version_comp("3:2.5.3-1", "3:2.5.3-1")
        self.assertEqual(output, 0)

    def test_version_segment_comp(self):
        output = esg_functions.version_segment_comp("2.3.4", "3.2.5")
        self.assertEqual(output, -1)

        output = esg_functions.version_segment_comp("2.3.4", "2.2.5")
        self.assertEqual(output, 1)

        output = esg_functions.version_segment_comp("3.2.5", "2.3.4")
        self.assertEqual(output, 1)

        output = esg_functions.version_segment_comp("3.2.5", "3.2.5")
        self.assertEqual(output, 0)

        output = esg_functions.version_segment_comp("3.2.5", "3.2")
        self.assertEqual(output, 1)

    def test_check_version_atleast(self):
        output = esg_functions.check_version_atleast("3.2.5", "6.0")
        self.assertEqual(output, 1)

        output = esg_functions.check_version_atleast("2.7.10", "2.9.5")
        self.assertEqual(output, 1)

        output = esg_functions.check_version_atleast("6.0", "3.2.5")
        self.assertEqual(output, 0)

        output = esg_functions.check_version_atleast("6.0", "6.0")
        self.assertEqual(output, 0)

    def test_check_version_between(self):
        output = esg_functions.check_version_between("3.2.5", "2.3.4", "6.0")
        self.assertEqual(output, 0)

        output = esg_functions.check_version_between("2.3.4", "3.2.5", "6.0")
        self.assertEqual(output, 1)

        output = esg_functions.check_version_between("6.0", "2.3.4", "3.2.5")
        self.assertEqual(output, 1)

        output = esg_functions.check_version_between("6.0", "3.2.5", "2.3.4")
        self.assertEqual(output, 1)

    def test_check_version(self):
        output = esg_functions.check_version("python", "2.7")
        self.assertEqual(output, 0)

        output = esg_functions.check_version("python", "2.9")
        self.assertEqual(output, 1)

        output = esg_functions.check_version("python", "2.9", "3.3")
        self.assertEqual(output, 1)

    def  test_check_version_with(self):
    	output = esg_functions.check_version_with("java", "java -version", "1.6.0")
    	self.assertEqual(output, 0)

    	output = esg_functions.check_version_with("git", "git --version", "1.6.0")
    	self.assertEqual(output, 0)

    def test_check_module_version(self):
        output = esg_functions.check_module_version("pylint", "1.6")
        self.assertEqual(output,0)

        output = esg_functions.check_module_version("pylint", "1.9")
        self.assertEqual(output,1)

    def test_get_current_esgf_library_version(self):
        output = esg_functions.get_current_esgf_library_version("esgf-security")
        self.assertEqual(output, 1)

if __name__ == '__main__':
    unittest.main()