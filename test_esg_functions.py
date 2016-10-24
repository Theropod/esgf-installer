#!/usr/bin/local/env python

import unittest
import esg_functions


class test_ESG_Functions(unittest.TestCase):

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

        output = esg_functions.version_segment_comp("3.2.5", "2.3.4")
        self.assertEqual(output, 1)

        output = esg_functions.version_segment_comp("3.2.5", "3.2.5")
        self.assertEqual(output, 0)

    def test_check_version_atleast(self):
        output = esg_functions.check_version_atleast("3.2.5", "6.0")
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

    def check_version(self):
    	output = esg_functions.check_version("python", "2.7")
    	self.assertEqual(output,0)

if __name__ == '__main__':
    unittest.main()