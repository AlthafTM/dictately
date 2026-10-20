from _support import ROOT
import unittest
from unittest.mock import Mock
from ui_preferences import POSITIONS, position_name, overlay_origin, desktop_microphones, resolve_microphone


class PreferencesTests(unittest.TestCase):
    def test_default_and_corner_placements(self):
        self.assertEqual(position_name(None),'Bottom center')
        self.assertEqual(position_name('invalid'),'Bottom center')
        bounds=(-1920,0,1920,1040)
        expected=[(-1898,982),(-1028,982),(-158,982),(-1898,22),(-1028,22),(-158,22)]
        for position,point in zip(POSITIONS.values(),expected):
            self.assertEqual(overlay_origin(bounds,(136,36),22,position),point)
        self.assertEqual(overlay_origin((0,0,80,20),(136,36),44,'top_right'),(0,0))

    def test_input_only_enumeration_and_persistent_selection(self):
        sound=Mock()
        sound.query_hostapis.return_value=[{'name':'Core Audio'}]
        sound.query_devices.return_value=[{'name':'Speaker','hostapi':0,'max_input_channels':0}, {'name':'USB mic','hostapi':0,'max_input_channels':1}]
        devices=desktop_microphones(sound)
        self.assertEqual(len(devices),1)
        preference={'name':'USB mic','host':'Core Audio'}
        self.assertIsNone(resolve_microphone(None,devices))
        self.assertEqual(resolve_microphone(preference,devices),1)
        devices[0]['index']=9
        self.assertEqual(resolve_microphone(preference,devices),9)
        with self.assertRaisesRegex(RuntimeError,'unavailable'):
            resolve_microphone(preference,[])
        with self.assertRaisesRegex(RuntimeError,'ambiguous'):
            resolve_microphone(preference,devices+devices)
        sound.RawInputStream.assert_not_called()
