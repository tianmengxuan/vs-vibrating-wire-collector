import unittest
from datetime import datetime, timedelta

from gui import VSCollectorGUI, RECENT_DATA_ONLINE_SECONDS


class FakeTree:
    columns = (
        'udid', 'site', 'status', 'ip', 'signal', 'voltage', 'packets',
        'freq', 'temp', 'calc', 'water', 'water_elevation', 'quality',
        'refresh_time',
    )

    def __init__(self):
        self.rows = {}

    def insert(self, parent, index, iid, values):
        self.rows[iid] = list(values)

    def set(self, iid, column, value=None):
        idx = self.columns.index(column)
        if value is None:
            return self.rows[iid][idx]
        self.rows[iid][idx] = value

    def item(self, iid, values=None):
        if values is not None:
            self.rows[iid] = list(values)
        return {'values': self.rows[iid]}


class FakeLabel:
    def __init__(self):
        self.text = ''

    def configure(self, **kwargs):
        if 'text' in kwargs:
            self.text = kwargs['text']


class FakeRoot:
    def __init__(self):
        self.after_calls = []

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))


class FakeDB:
    def __init__(self):
        self.snapshots = []

    def is_connected(self):
        return True

    def insert_device_snapshot(self, **kwargs):
        self.snapshots.append(kwargs)
        return True


def build_gui():
    gui = VSCollectorGUI.__new__(VSCollectorGUI)
    gui.devices = {}
    gui.site_names = {}
    gui.device_formulas = {}
    gui._default_fm = {'K': 1.0, 'f0': 0.0, 'alpha': 0.0, 'T0': 20.0, 'elev': 0.0}
    gui._GARBAGE_UDID = ('', 'UNKNOWN', 'UNKNOWN_DEVICE', 'SL651_STR', 'SL651_HEX', 'SL651_UNKNOWN')
    gui._tree_sort_column = None
    gui.tree = FakeTree()
    gui.st_online = FakeLabel()
    gui.db_manager = None
    gui.root = FakeRoot()
    gui._log = lambda *args, **kwargs: None
    gui._update_combo = lambda: None
    gui._apply_tree_sort = lambda: None
    return gui


class GUIOnlineStatusTests(unittest.TestCase):
    def test_recent_data_disconnect_keeps_row_online(self):
        gui = build_gui()

        gui._dev_data('DEV_001', {'channels': [], 'addr': '10.0.0.2:10000'})
        gui._dev_offline('DEV_001')

        self.assertTrue(gui.devices['DEV_001']['online'])
        self.assertEqual(gui.tree.set('DEV_001', 'status'), '● 在线')
        self.assertEqual(gui.st_online.text, '在线: 1')

    def test_stale_data_disconnect_marks_row_offline(self):
        gui = build_gui()
        gui._dev_data('DEV_001', {'channels': []})
        gui.devices['DEV_001']['last_data_time'] = (
            datetime.now() - timedelta(seconds=RECENT_DATA_ONLINE_SECONDS + 1)
        )

        gui._dev_offline('DEV_001')

        self.assertFalse(gui.devices['DEV_001']['online'])
        self.assertEqual(gui.tree.set('DEV_001', 'status'), '○ 离线')
        self.assertEqual(gui.st_online.text, '在线: 0')

    def test_stale_checker_expires_rows_without_disconnect_event(self):
        gui = build_gui()
        gui._dev_data('DEV_001', {'channels': []})
        gui.devices['DEV_001']['last_data_time'] = (
            datetime.now() - timedelta(seconds=RECENT_DATA_ONLINE_SECONDS + 1)
        )

        gui._expire_stale_devices()

        self.assertFalse(gui.devices['DEV_001']['online'])
        self.assertEqual(gui.tree.set('DEV_001', 'status'), '○ 离线')
        self.assertEqual(gui.root.after_calls[-1][0], 30000)

    def test_empty_channel_packet_clears_previous_channel_values(self):
        gui = build_gui()

        gui._dev_data('DEV_001', {
            'channels': [{
                'channel': 1,
                'frequency': 2220.3,
                'temp': 20.9,
                'data_quality': 'VALID',
            }],
        })
        gui._dev_data('DEV_001', {'channels': []})

        self.assertEqual(gui.devices['DEV_001']['channels'], {})
        self.assertEqual(gui.tree.set('DEV_001', 'freq'), '')
        self.assertEqual(gui.tree.set('DEV_001', 'temp'), '')
        self.assertEqual(gui.tree.set('DEV_001', 'calc'), '')
        self.assertEqual(gui.tree.set('DEV_001', 'quality'), 'NO_CHANNEL')

    def test_empty_channel_snapshot_uses_no_channel_without_error_log(self):
        gui = build_gui()
        gui.db_manager = FakeDB()
        logs = []
        gui._log = lambda lv, msg: logs.append((lv, msg))

        gui._dev_data('DEV_001', {'channels': [], 'signal': 25, 'voltage': 13.25})

        self.assertEqual(gui.db_manager.snapshots[-1]['frequency'], None)
        self.assertEqual(gui.db_manager.snapshots[-1]['temperature'], None)
        self.assertEqual(gui.db_manager.snapshots[-1]['pressure'], None)
        self.assertEqual(gui.db_manager.snapshots[-1]['water_level'], None)
        self.assertEqual(gui.db_manager.snapshots[-1]['status'], 'NO_CHANNEL')
        self.assertFalse(any(lv == 'ERROR' and '入库失败' in msg for lv, msg in logs))


if __name__ == '__main__':
    unittest.main()
