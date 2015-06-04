# -*- coding: utf-8 -*-

import logging
import threading
import importlib
import numbers
import re
from sleekxmpp import ClientXMPP
from sleekxmpp.exceptions import IqTimeout, IqError


class HipChat(threading.Thread):
    __commands = []

    def __init__(self, config):

        threading.Thread.__init__(self)

        self.config = config
        self.client = self.setup_xmpp_client()
        self.load_plugins(self.config.get('plugins', []))

    def run(self):
        connected = self.client.connect()
        if not connected:
            raise SarahHipChatException('Coudn\'t connect to server.')
        self.client.process(block=True)

    def setup_xmpp_client(self):
        client = ClientXMPP(self.config['jid'], self.config['password'])

        if 'proxy_setting' in self.config:
            client.use_proxy = True
            client.proxy_config = {}
            for key in ('host', 'port', 'username', 'password'):
                client.proxy_config[key] = self.config.get(key, None)

        # TODO check later
        # client.add_event_handler('ssl_invalid_cert', lambda cert: True)

        client.add_event_handler('roster_update', self.join_rooms)
        client.add_event_handler('session_start', self.session_start)
        client.add_event_handler('message', self.message)
        client.register_plugin('xep_0045')
        client.register_plugin('xep_0203')

        return client

    def load_plugins(self, plugins):
        for module_config in plugins:
            self.load_plugin(module_config[0])

    def load_plugin(self, module_name):
        try:
            importlib.import_module(module_name)
        except Exception as e:
            logging.error(e)
            logging.error('Failed to load %s. Skipping.' % module_name)
            return

        logging.info('Loaded plugin. %s' % module_name)

    def session_start(self, event):
        self.client.send_presence()

        # http://sleekxmpp.readthedocs.org/en/latest/getting_started/echobot.html
        # It is possible for a timeout to occur while waiting for the server to
        # respond, which can happen if the network is excessively slow or the
        # server is no longer responding. In that case, an IqTimeout is raised.
        # Similarly, an IqError exception can be raised if the request
        # contained
        # bad data or requested the roster for the wrong user.
        try:
            self.client.get_roster()
        except IqTimeout as e:
            raise SarahHipChatException('Timeout occured while getting roster.'
                                        'Error type: %s. Condition: %s.' %
                                        e.etype, e.condition)
        except IqError as e:
            # ret['type'] == 'error'
            raise SarahHipChatException('Timeout occured while getting roster.'
                                        'Error type: %s. Condition: %s.'
                                        'Content: %s.' %
                                        e.etype, e.condition, e.text)
        except:
            raise SarahHipChatException('Unknown error occured.')

    def join_rooms(self, event):
        if 'rooms' not in self.config:
            return

        # You MUST explicitely join rooms to receive message via XMPP interface
        for room in self.config['rooms']:
            self.client.plugin['xep_0045'].joinMUC(room,
                                                   self.config.get('nick', ''),
                                                   maxhistory=None,
                                                   wait=True)

    def message(self, msg):
        if msg['delay']['stamp']:
            # Avoid answering to all past messages when joining the room.
            # xep_0203 plugin required.
            # http://xmpp.org/extensions/xep-0203.html
            return

        if msg['type'] in ('normal', 'chat'):
            # msg.reply("Thanks for sending\n%(body)s" % msg).send()
            pass

        elif msg['type'] == 'groupchat':
            # Don't talk to yourself. It's freaking people out.
            group_plugin = self.client.plugin['xep_0045']
            my_nick = group_plugin.ourNicks[msg.get_mucroom()]
            sender_nick = msg.get_mucnick()
            if my_nick == sender_nick:
                return

        for command in self.commands:
            if msg['body'].startswith(command[0]):
                plugin_config = {}
                for (name, config) in self.config.get('plugins', ()):
                    module_name = command[2]
                    if module_name != name or config is None:
                        continue
                    plugin_config = config

                text = re.sub(r'{0}\s+'.format(command[0]), '', msg['body'])
                ret = command[1]({'original_text': msg['body'],
                                  'text': text,
                                  'from': msg['from']},
                                 plugin_config)

                if isinstance(ret, str):
                    msg.reply(ret).send()
                elif isinstance(ret, numbers.Integral):
                    msg.reply(str(ret)).send()
#                elif isinstance(ret, dict):
#                    pass
                else:
                    logging.error('Malformed returning value. '
                                  'Command: %s. Value: %s.' %
                                  (command[1], str(ret)))

    @property
    def commands(self):
        return self.__commands

    @classmethod
    def command(cls, name):
        def wrapper(func):
            def wrapped_function(*args, **kwargs):
                return func(*args, **kwargs)
            if name in [command_set[0] for command_set in cls.__commands]:
                logging.info("Skip duplicate command. "
                             "module: %s. command: %s." %
                             (func.__module__, name))
            else:
                cls.add_command(name, wrapped_function, func.__module__)
        return wrapper

    @classmethod
    def add_command(cls, name, func, module_name):
        cls.__commands.append((name, func, module_name))

    def stop(self):
        logging.info('STOP HIPCHAT INTEGRATION')
        if hasattr(self, 'client') and self.client is not None:
            logging.info('DISCONNECTING FROM HIPCHAT SERVER')
            self.client.socket.recv_data(self.client.stream_footer)
            self.client.disconnect()


class SarahHipChatException(Exception):
    pass
