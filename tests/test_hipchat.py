# -*- coding: utf-8 -*-
import pytest
import logging
from threading import Thread
from apscheduler.triggers.interval import IntervalTrigger
from sleekxmpp import ClientXMPP
from sleekxmpp.test import TestSocket
from sleekxmpp.stanza import Message
from sleekxmpp.exceptions import IqTimeout, IqError
from mock import MagicMock, call, patch
from sarah.hipchat import HipChat, SarahHipChatException
import sarah.plugins.simple_counter
import types


class MockXMPP(ClientXMPP):
    def __init__(self, *args):
        super().__init__(*args, sasl_mech=None)
        self._id_prefix = ''
        self._disconnect_wait_for_threads = False
        self.default_lang = None
        self.peer_default_lang = None
        self.set_socket(TestSocket())
        self.auto_reconnect = False
        self.state._set_state('connect')
        self.socket.recv_data(self.stream_header)
        self.use_message_ids = False

sarah.hipchat.ClientXMPP = MockXMPP


class TestInit(object):
    def test_init(self):
        hipchat = HipChat({'nick': 'Sarah',
                           'jid': 'test@localhost',
                           'password': 'password',
                           'plugins': (('sarah.plugins.simple_counter', {}),
                                       ('sarah.plugins.echo', {})),
                           'proxy': {'host': 'localhost',
                                     'port': 1234,
                                     'username': 'homers',
                                     'password': 'mypassword'}})

        assert isinstance(hipchat, HipChat) is True
        assert isinstance(hipchat.client, ClientXMPP) is True

        assert hipchat.commands[0][0] == '.count'
        assert hipchat.commands[0][2] == 'sarah.plugins.simple_counter'
        assert isinstance(hipchat.commands[1][1], types.FunctionType) is True

        assert hipchat.commands[1][0] == '.echo'
        assert isinstance(hipchat.commands[1][1], types.FunctionType) is True
        assert hipchat.commands[1][2] == 'sarah.plugins.echo'

        assert hipchat.client.use_proxy is True
        assert hipchat.client.proxy_config == {'host': 'localhost',
                                               'port': 1234,
                                               'username': 'homers',
                                               'password': 'mypassword'}

    def test_non_existing_plugin(self):
        logging.warning = MagicMock()
        HipChat({'nick': 'Sarah',
                 'jid': 'test@localhost',
                 'password': 'password',
                 'plugins': (('spam.ham.egg.onion', {}), )})
        assert logging.warning.call_count == 1
        assert logging.warning.call_args == call(
                'Failed to load spam.ham.egg.onion. '
                'No module named \'spam\'. Skipping.')

    def test_connection_fail(self):
        hipchat = HipChat({'nick': 'Sarah',
                           'jid': 'test@localhost',
                           'password': 'password'})

        with patch.object(
                hipchat.client,
                'connect',
                return_value=False) as _mock_connect:

            with pytest.raises(SarahHipChatException) as e:
                hipchat.run()

            assert e.value.args[0] == 'Couldn\'t connect to server.'
            assert _mock_connect.call_count == 1

    def test_run(self):
        hipchat = HipChat({'nick': 'Sarah',
                           'jid': 'test@localhost',
                           'password': 'password'})

        with patch.object(hipchat.client, 'connect', return_value=True):

            with patch.object(
                    hipchat.scheduler,
                    'start',
                    return_value=True) as mock_scheduler_start:

                with patch.object(
                        hipchat.client,
                        'process',
                        return_value=True) as mock_client_process:

                    hipchat.run()

                assert mock_scheduler_start.call_count == 1
                assert mock_client_process.call_count == 1


class TestFindCommand(object):
    @pytest.fixture
    def hipchat(self, request):
        # NO h.start() for this test
        h = HipChat({'nick': 'Sarah',
                     'jid': 'test@localhost',
                     'password': 'password',
                     'plugins': (
                         ('sarah.plugins.simple_counter', {'spam': 'ham'}),
                         ('sarah.plugins.echo', ))})
        return h

    def test_no_corresponding_command(self, hipchat):
        command = hipchat.find_command('egg')
        assert command is None

    def test_echo(self, hipchat):
        command = hipchat.find_command('.echo spam ham')
        assert command['config'] == {}
        assert command['name'] == '.echo'
        assert command['module_name'] == 'sarah.plugins.echo'
        assert isinstance(command['function'], types.FunctionType) is True

    def test_count(self, hipchat):
        command = hipchat.find_command('.count spam')
        assert command['config'] == {'spam': 'ham'}
        assert command['name'] == '.count'
        assert command['module_name'] == 'sarah.plugins.simple_counter'
        assert isinstance(command['function'], types.FunctionType) is True


class TestMessage(object):
    @pytest.fixture(scope='function')
    def hipchat(self, request):
        h = HipChat({'nick': 'Sarah',
                     'jid': 'test@localhost',
                     'password': 'password',
                     'plugins': (('sarah.plugins.simple_counter', {}),
                                 ('sarah.plugins.echo',))})
        Thread(target=h.run)
        request.addfinalizer(h.stop)

        return h

    def test_skip_message(self, hipchat):
        msg = Message(hipchat.client, stype='normal')
        msg['body'] = 'test body'

        msg.reply = MagicMock()

        hipchat.message(msg)
        assert msg.reply.call_count == 0

    def test_echo_message(self, hipchat):
        msg = Message(hipchat.client, stype='normal')
        msg['body'] = '.echo spam'

        msg.reply = MagicMock()

        hipchat.message(msg)
        assert msg.reply.call_count == 1
        assert msg.reply.call_args == call('spam')

    def test_count_message(self, hipchat):
        msg = Message(hipchat.client,
                      stype='normal',
                      sfrom='123_homer@localhost/Oklahomer')
        msg['body'] = '.count ham'

        msg.reply = MagicMock()

        hipchat.message(msg)
        assert msg.reply.call_count == 1
        assert msg.reply.call_args == call('1')

        hipchat.message(msg)
        assert msg.reply.call_count == 2
        assert msg.reply.call_args == call('2')

        msg['body'] = '.count egg'
        hipchat.message(msg)
        assert msg.reply.call_count == 3
        assert msg.reply.call_args == call('1')

        stash = vars(sarah.plugins.simple_counter).get('__stash', {})
        assert stash == {'123_homer@localhost/Oklahomer': {'ham': 2, 'egg': 1}}


class TestSessionStart(object):
    @pytest.fixture
    def hipchat(self, request):
        # NO h.start() for this test
        return HipChat({'nick': 'Sarah',
                        'jid': 'test@localhost',
                        'password': 'password',
                        'plugins': ()})

    def throw_iq_timeout(self):
        raise IqTimeout(None)

    def throw_iq_error(self):
        raise IqError({'error': {'condition': 'ham',
                                 'text': 'egg',
                                 'type': 'spam'}})

    def throw_exception(self):
        raise Exception('spam.ham.egg')

    def test_timeout(self, hipchat):
        with patch.object(hipchat.client, 'send_presence', return_value=None):

            with patch.object(
                    hipchat.client,
                    'get_roster',
                    side_effect=self.throw_iq_timeout) as _mock_get_roster:

                with pytest.raises(SarahHipChatException) as e:
                    hipchat.session_start(None)

                assert _mock_get_roster.call_count == 1
                assert e.value.args[0] == (
                        'Timeout occured while getting roster. '
                        'Error type: cancel. '
                        'Condition: remote-server-timeout.')

    def test_unknown_error(self, hipchat):
        with patch.object(hipchat.client, 'send_presence', return_value=None):

            with patch.object(
                    hipchat.client,
                    'get_roster',
                    side_effect=self.throw_exception) as _mock_get_roster:

                with pytest.raises(SarahHipChatException) as e:
                    hipchat.session_start(None)

                assert _mock_get_roster.call_count == 1
                assert e.value.args[0] == (
                        'Unknown error occured: spam.ham.egg.')

    def test_iq_error(self, hipchat):
        with patch.object(hipchat.client, 'send_presence', return_value=None):

            with patch.object(
                    hipchat.client,
                    'get_roster',
                    side_effect=self.throw_iq_error) as _mock_get_roster:

                with pytest.raises(SarahHipChatException) as e:
                    hipchat.session_start(None)

                assert _mock_get_roster.call_count == 1
                assert e.value.args[0] == (
                        'IQError while getting roster. '
                        'Error type: spam. Condition: ham. Content: egg.')


class TestJoinRooms(object):
    def test_success(self):
        h = HipChat({'nick': 'Sarah',
                     'jid': 'test@localhost',
                     'rooms': ['123_homer@localhost'],
                     'password': 'password',
                     'plugins': ()})

        with patch.object(h.client.plugin['xep_0045'].xmpp,
                          'send',
                          return_value=None) as _mock_send:

            h.join_rooms(None)

            assert _mock_send.call_count == 1
            assert h.client.plugin['xep_0045'].rooms == {
                    '123_homer@localhost': {}}
            assert h.client.plugin['xep_0045'].ourNicks == {
                    '123_homer@localhost': h.config['nick']}

    def test_no_setting(self):
        h = HipChat({'nick': 'Sarah',
                     'jid': 'test@localhost',
                     'password': 'password',
                     'plugins': ()})

        with patch.object(h.client.plugin['xep_0045'].xmpp,
                          'send',
                          return_value=None) as _mock_send:

            h.join_rooms(None)

            assert _mock_send.call_count == 0
            assert h.client.plugin['xep_0045'].rooms == {}
            assert h.client.plugin['xep_0045'].ourNicks == {}


class TestSchedule(object):
    @pytest.fixture
    def hipchat(self, request):
        # NO h.start() for this test
        h = HipChat({'nick': 'Sarah',
                     'jid': 'test@localhost',
                     'password': 'password',
                     'plugins': (
                         ('sarah.plugins.bmw_quotes', {
                             'rooms': ('123_homer@localhost', ),
                             'interval': 5}))})
        return h

    def test_missing_config(self):
        logging.warning = MagicMock()

        h = HipChat({'nick': 'Sarah',
                     'jid': 'test@localhost',
                     'password': 'password',
                     'plugins': (('sarah.plugins.bmw_quotes', ), )})
        h.add_schedule_jobs(h.schedules)

        assert logging.warning.call_count == 1
        assert logging.warning.call_args == call(
                'Missing configuration for schedule job. '
                'sarah.plugins.bmw_quotes. Skipping.')

    def test_missing_rooms_config(self):
        logging.warning = MagicMock()

        h = HipChat({'nick': 'Sarah',
                     'jid': 'test@localhost',
                     'password': 'password',
                     'plugins': (('sarah.plugins.bmw_quotes', {}), )})
        h.add_schedule_jobs(h.schedules)

        assert logging.warning.call_count == 1
        assert logging.warning.call_args == call(
                'Missing rooms configuration for schedule job. '
                'sarah.plugins.bmw_quotes. Skipping.')

    def test_add_schedule_job(self):
        hipchat = HipChat({
            'nick': 'Sarah',
            'jid': 'test@localhost',
            'password': 'password',
            'plugins': (('sarah.plugins.bmw_quotes',
                         {'rooms': ('123_homer@localhost', )}), )})
        hipchat.add_schedule_jobs(hipchat.schedules)

        jobs = hipchat.scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == 'sarah.plugins.bmw_quotes.bmw_quotes'
        assert isinstance(jobs[0].trigger, IntervalTrigger) is True
        assert jobs[0].trigger.interval_length == 300
        assert isinstance(jobs[0].func, types.FunctionType) is True
