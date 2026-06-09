import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, call, patch

from tcp_server.server import TCPServer
from tcp_server.session import IDLE_TIMEOUT_SECONDS


class FakeSession:
    def __init__(self):
        self.udid = ''
        self.addr = ('127.0.0.1', 10000)

    def set_udid(self, udid):
        self.udid = udid


def parse_result_for(udid):
    return {
        'udid': udid,
        'raw_packet': {
            'raw_hex': '0102',
            'raw_str': '',
        },
        'protocol': 'STR1.0',
        'status': 'OK',
        'channels': [],
        'timestamp': None,
    }


class TCPServerOnlineStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_udid_data_marks_device_online(self):
        db = MagicMock()
        db.is_connected.return_value = True
        db.insert_raw_packet.return_value = 1
        server = TCPServer(db_manager=db)
        session = FakeSession()

        with patch('protocol.dispatcher.identify_and_parse',
                   return_value=parse_result_for('DEV_001')):
            await server._on_device_data(session, b'\x01\x02')

        db.update_device_online_status.assert_any_call('DEV_001', True)

    async def test_recent_data_disconnect_does_not_mark_database_offline(self):
        db = MagicMock()
        db.is_connected.return_value = True
        server = TCPServer(db_manager=db)
        server._last_data_seen['DEV_001'] = datetime.now()
        server._schedule_stale_offline = MagicMock()
        session = FakeSession()
        session.set_udid('DEV_001')

        await server._on_device_disconnect(session)

        self.assertNotIn(
            call('DEV_001', False),
            db.update_device_online_status.call_args_list,
        )
        server._schedule_stale_offline.assert_called_once_with(
            'DEV_001', server._last_data_seen['DEV_001']
        )

    async def test_stale_data_disconnect_marks_database_offline(self):
        db = MagicMock()
        db.is_connected.return_value = True
        server = TCPServer(db_manager=db)
        server._last_data_seen['DEV_001'] = (
            datetime.now() - timedelta(seconds=IDLE_TIMEOUT_SECONDS + 1)
        )
        server._schedule_stale_offline = MagicMock()
        session = FakeSession()
        session.set_udid('DEV_001')

        await server._on_device_disconnect(session)

        db.update_device_online_status.assert_any_call('DEV_001', False)
        server._schedule_stale_offline.assert_not_called()

    async def test_unknown_and_garbage_udid_do_not_mark_online(self):
        for udid in ('UNKNOWN', 'UNKNOWN_DEVICE', 'SL651_STR', 'HTTP_GET'):
            with self.subTest(udid=udid):
                db = MagicMock()
                db.is_connected.return_value = True
                server = TCPServer(db_manager=db)
                session = FakeSession()

                with patch('protocol.dispatcher.identify_and_parse',
                           return_value=parse_result_for(udid)):
                    await server._on_device_data(session, b'\x01\x02')

                db.update_device_online_status.assert_not_called()


if __name__ == '__main__':
    unittest.main()
