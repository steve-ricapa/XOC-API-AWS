"""Pure parser/allowlist guards: no GDB, Docker, network, or target launched."""
import unittest
from unittest.mock import patch

from ic1r5_observer import MIParser, MIParseError, Observer, safe_frame


class ParserTests(unittest.TestCase):
    def test_command_result(self):
        result=MIParser.record('17^done')
        self.assertEqual(result['command_id'],17)
        self.assertEqual(result['category'],'done')

    def test_nested_frames(self):
        result=MIParser.record('5^done,stack=[frame={level="0",func="Py_FinalizeEx",args=[]},frame={level="1",func="main"}]')
        self.assertEqual(len(result['data']['stack']),2)
        self.assertEqual(result['data']['stack'][0][1]['func'],'Py_FinalizeEx')

    def test_async(self):
        result=MIParser.record('*stopped,thread-id="1",frame={level="0",func="read"}')
        self.assertEqual(result['kind'],'*')
        self.assertIsNone(result['command_id'])

    def test_stream_not_decoded(self):
        result=MIParser.record('~"synthetic-private-content\\n"')
        self.assertEqual(result['data'],{})
        self.assertNotIn('synthetic-private-content',repr(result))

    def test_missing_delimiter(self):
        with self.assertRaises(MIParseError):
            MIParser.record('4^done,stack=[frame={func="main"}')

    def test_oversized(self):
        with self.assertRaises(MIParseError):
            MIParser.record('1^done,data="'+'a'*131073+'"')

    def test_allowlist_drops_sensitive_fields(self):
        frame=safe_frame(dict(level='0',func='read',args=['synthetic-private-content'],
            fullname='/private/workspace/secret',addr='0x123456',locals='hidden',env='hidden',
            **{'from':'/usr/lib64/libc.so.6'}))
        self.assertEqual(frame,dict(frame=0,symbol='read',library='libc.so.6'))

    def test_symbol_injection_dropped(self):
        frame=safe_frame(dict(func='bad\nprivate',level='0'))
        self.assertIsNone(frame['symbol'])

    def test_frame_limit(self):
        self.assertIsNone(safe_frame(dict(level='12',func='main')))

    def test_identity_rejected_before_proc(self):
        binding=dict(generation='known',target=dict(pid=123),container_id='own',
                     container_label='own',parent_start=1)
        observer=Observer(binding)
        observer.emit=lambda *a,**k:None
        with patch('ic1r5_observer.identity',side_effect=AssertionError('must not inspect')):
            self.assertFalse(observer.validate({**binding,'generation':'other'}))

    def test_timing_does_not_replace_target_state(self):
        observer=Observer(dict(generation='unit',target=dict(pid=123)))
        observer.emit=lambda *a,**k:None
        observer.validate=lambda requested:True
        observer.command=lambda *a,**k:dict(category='done')
        observer.frames=[dict(frame=0,symbol='main',library=None)]
        expected=dict(state='R',tracer_pid=0)
        def recover():
            observer.stop_samples=[(1.0,'R'),(1.01,'t'),(1.02,'t'),(1.03,'R')]
            observer.watchdog_done.set()
            return expected
        observer.recover=recover
        with patch('ic1r5_observer.state',return_value=expected):
            result=observer.observe({})
        self.assertEqual(result['target_after'],expected)
        self.assertIsInstance(result['target_after'],dict)


if __name__=='__main__':
    unittest.main()
