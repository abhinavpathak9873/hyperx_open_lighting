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
    def run_worker(self,mode='Sync with OpenRGB',enabled=True,renamed=False):
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
        self.assertEqual(new['microphone']['mode'],'Sync with OpenRGB')
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

    def test_legacy_follow_alias_and_sync_for_all(self):
        settings={k:{**v,'mode':'Sync with OpenRGB'} for k,v in app.DEFAULT.items()}
        settings['microphone']['mode']='Follow OpenRGB'
        result=app.validate(settings)
        self.assertTrue(all(v['mode']=='Sync with OpenRGB' for v in result.values()))

    def test_readonly_sync_reader_and_failure_retains_color(self):
        sdk=FakeSDK()
        shared={'settings':{k:{**v,'mode':'Sync with OpenRGB'} for k,v in app.DEFAULT.items()}}
        with patch.object(openrgb,'SDK',return_value=sdk):
            openrgb.sync_worker(shared,threading.Lock(),StopAfter(2))
        self.assertEqual(shared['openrgb_sync']['color'],[12,34,56])
        self.assertEqual(sdk.sent,[])
        with patch.object(openrgb,'SDK',side_effect=ConnectionRefusedError('offline')):
            openrgb.sync_worker(shared,threading.Lock(),StopAfter(1))
        self.assertEqual(shared['openrgb_sync']['color'],[12,34,56])
        self.assertEqual(shared['openrgb_sync']['error'],'offline')

    def test_usb_sync_uses_shared_color_without_sdk_io(self):
        from unittest.mock import MagicMock
        device=MagicMock()
        device.path='fake';device.acks=0;device.info={}
        shared={'settings':{k:{**v,'mode':'Sync with OpenRGB'} for k,v in app.DEFAULT.items()},
                'devices':{},'openrgb_sync':{'color':[12,34,56]}}
        with patch.object(app,'Device',return_value=device), patch.object(app,'find_device',return_value='fake'), patch.object(openrgb,'SDK') as sdk:
            app.device_worker('keyboard',shared,threading.Lock(),StopAfter(2))
        sdk.assert_not_called()
        self.assertEqual(device.frame.call_count,2)
        self.assertEqual(device.frame.call_args.args[0],[(12,34,56)]*103)
