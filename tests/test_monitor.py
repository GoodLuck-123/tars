import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
SPEC=importlib.util.spec_from_file_location('monitor',Path(__file__).resolve().parents[1]/'runtime/monitor.py')
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)


class MonitorTests(unittest.TestCase):
    def test_high_volume_is_silent_but_errors_are_visible(self):
        with tempfile.TemporaryDirectory() as d:
            m=M.Monitor(d);self.addCleanup(m.events.close)
            for i in range(1000):m.line(f'normal frame {i}','livo.log',i)
            self.assertEqual(m.seq,0)
            m.line('[ERROR] unknown upstream error 42','livo.log',1001)
            self.assertEqual(m.state['errors'],1)
            self.assertEqual(m.seq,1)
            self.assertIn('unknown upstream',next(iter(m.state['alerts'].values()))['message'])

    def test_repeats_count_without_flood(self):
        with tempfile.TemporaryDirectory() as d:
            m=M.Monitor(d,repeat_s=100);self.addCleanup(m.events.close)
            for i in range(100):m.line(f'[WARN] delay {i}.0','livo.log',i)
            self.assertEqual(m.state['warnings'],100)
            self.assertEqual(m.seq,1)
            self.assertEqual(next(iter(m.state['alerts'].values()))['count'],100)

    def test_progress_is_processing_not_player_output(self):
        with tempfile.TemporaryDirectory() as d:
            m=M.Monitor(d);self.addCleanup(m.events.close);m.line('Bag Time 100.0','play.log',0)
            self.assertNotIn('processed_stamp',m.state)
            m.line('LIVO_PROGRESS stamp=123.25 mode=1 lidar_queue=2 image_queue=3','livo.log',10)
            self.assertEqual(m.state['processed_stamp'],123.25)
            self.assertEqual(m.state['image_queue'],3)

    def test_rotation_partial_lines_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            logs=Path(d)/'logs';logs.mkdir();p=logs/'livo.log';p.write_text('[WARN] first\n[ERROR] partial')
            m=M.Monitor(d);self.addCleanup(m.events.close);m.poll();self.assertEqual(m.state['warnings'],1);self.assertEqual(m.state['errors'],0)
            with p.open('a') as f:f.write(' finished\n')
            m.poll();self.assertEqual(m.state['errors'],1)
            m.events.close();m=M.Monitor(d);self.addCleanup(m.events.close);m.poll();self.assertEqual(m.state['errors'],1)
            p.rename(logs/'old.log');p.write_text('[WARN] rotated\n');m.poll()
            self.assertGreaterEqual(m.state['warnings'],2)

    def test_only_exact_cross_source_lines_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as d:
            m=M.Monitor(d);self.addCleanup(m.events.close)
            m.line('[WARN] [123.1] bad input','livo.log',0,now=1)
            m.line('[WARN] [123.1] bad input','mirror.log',0,now=1.1)
            m.line('[WARN] [123.2] bad input','other-node.log',0,now=1.2)
            self.assertEqual(m.state['warnings'],2)


if __name__=='__main__':unittest.main()
