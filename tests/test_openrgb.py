"""SDK isolation, migration and device targeting. Never connect to real hardware."""
import struct
import threading
import unittest
from unittest.mock import patch
from hyperx_open_lighting import app, openrgb


def fixture(name=openrgb.NAME, colors=bytes([12, 34, 56, 0])*108):
    def string(s):
        b=s.encode()+b'\0'
        return struct.pack('<H',len(b))+b
    body=struct.pack('<I',0)+string(name)+string('')*5
    body+=struct.pack('<HIHHH',0,0,0,0,len(colors)//4)+colors
    return struct.pack('<I',4+len(body))+body


class FakeSDK:
    def __init__(self, renamed=False):
        self.sent=[]; self.closed=False; self.colors=bytes([12,34,56,0])*108
        self.renamed=renamed
    def devices(self):
        return [(4,openrgb.NAME,self.colors)]
    def request(self,*args):
        return fixture('Other device' if self.renamed else openrgb.NAME,self.colors)
    def send(self,command,data=b'',device=0):
        self.sent.append((command,data,device))
        if command==1050:self.colors=data[6:]
    def close(self):self.closed=True


class StopAfter:
    def __init__(self,n):self.n=n
    def is_set(self):return self.n<=0
    def wait(self,delay):self.n-=1


class OpenRGBTest(unittest.TestCase):
    def run_worker(self,mode='Follow OpenRGB',enabled=True,renamed=False):
        sdk=FakeSDK(renamed)
        shared={'settings':{'microphone':{**app.DEFAULT['microphone'],'mode':mode,'enabled':enabled}},'devices':{}}
        with patch.object(openrgb,'SDK',return_value=sdk), patch.object(app.os,'open') as hid:
            openrgb.worker(shared,threading.Lock(),StopAfter(2),app.render)
        hid.assert_not_called()
        return sdk,shared['devices']['microphone']
    def test_follow_and_pause_never_write(self):
        for enabled in (True,False):
            sdk,state=self.run_worker(enabled=enabled)
            self.assertEqual(sdk.sent,[])
    def test_static_sends_once_and_verifies(self):
        sdk,state=self.run_worker('Static')
        self.assertEqual([x[0] for x in sdk.sent],[1100,1050])
        self.assertEqual(state['frames_sent'],1)
        self.assertTrue(sdk.closed)
    def test_index_change_never_colors_other_device(self):
        sdk,state=self.run_worker('Static',renamed=True)
        self.assertEqual(sdk.sent,[])
        self.assertIn('changed',state['state'])
    def test_old_settings_migrate_to_follow(self):
        old={k:dict(app.DEFAULT[k]) for k in ('keyboard','mouse')}
        new=app.validate(old)
        self.assertEqual(new['keyboard'],old['keyboard'])
        self.assertEqual(new['mouse'],old['mouse'])
        self.assertEqual(new['microphone']['mode'],'Follow OpenRGB')
        self.assertNotIn('microphone',old)
    def test_parser_rejects_truncation(self):
        raw=fixture()
        self.assertEqual(openrgb.controller(raw)[0],openrgb.NAME)
        for n in (0,3,20,len(raw)-1):
            with self.assertRaises(ValueError):openrgb.controller(raw[:n])
    def test_payload_and_wave(self):
        colors=app.render('microphone',{**app.DEFAULT['microphone'],'mode':'Rainbow wave'},0)
        self.assertEqual(len(set(colors)),108)
        payload=openrgb.color_payload(colors)
        self.assertEqual(len(payload),438)
        self.assertEqual(struct.unpack('<IH',payload[:6]),(438,108))
        with self.assertRaises(ValueError):openrgb.color_payload(colors[:1])
