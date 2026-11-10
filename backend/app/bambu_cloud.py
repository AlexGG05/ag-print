"""Read-only Bambu cloud MQTT telemetry. Not an official supported Bambu API."""
import copy
import json
import os
import ssl
import threading
import time
from datetime import datetime, timezone
from paho.mqtt import client as mqtt


def _number(value):
    try:
        return float(value) if value is not None and value != '' else None
    except (TypeError, ValueError):
        return None


def _slot(ams, index):
    try:
        slot = int(index)
    except (ValueError, TypeError):
        return None
    # Each AMS unit normally holds four trays. External spool is excluded.
    if slot < 0 or slot >= 4:
        return None
    return slot


class CloudMonitor:
    def __init__(self):
        self.serial = os.getenv('BAMBU_SERIAL', '').strip().upper()
        self.user_id = os.getenv('BAMBU_USER_ID', '').strip()
        self.token = os.getenv('BAMBU_ACCESS_TOKEN', '').strip()
        self.host = os.getenv('BAMBU_MQTT_HOST', 'us.mqtt.bambulab.com')
        self.lock = threading.Lock()
        self.client = None
        self.connected = False
        self.state = {
            'connection_status': 'not_configured', 'connection_note': 'Cloud-Zugangsdaten fehlen',
            'last_seen': None, 'print_state': None, 'progress_percent': None,
            'remaining_minutes': None, 'nozzle_temp': None, 'nozzle_target': None,
            'bed_temp': None, 'bed_target': None, 'chamber_temp': None,
            'file_name': None, 'layer': None, 'total_layers': None,
            'ams': [], 'ams_tray_now': None,
        }

    def start(self):
        if not all([self.serial, self.user_id, self.token]):
            return
        with self.lock:
            self.state['connection_status'] = 'connecting'
            self.state['connection_note'] = 'Verbinde mit Bambu Cloud MQTT'
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'agprint-{os.getpid()}-{int(time.time())}', protocol=mqtt.MQTTv311)
            client.username_pw_set('u_' + self.user_id.removeprefix('u_'), self.token)
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message
            client.connect_async(self.host, 8883, keepalive=60)
            client.loop_start()
            self.client = client
        except Exception as exc:
            with self.lock:
                self.state['connection_status'] = 'error'
                self.state['connection_note'] = f'MQTT-Start fehlgeschlagen: {type(exc).__name__}'

    def stop(self):
        if self.client:
            self.client.disconnect()
            self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        with self.lock:
            if reason_code.is_failure:
                self.connected = False
                self.state['connection_status'] = 'error'
                self.state['connection_note'] = f'MQTT-Login fehlgeschlagen: {reason_code}'
                return
            self.state['connection_status'] = 'connected'
            self.state['connection_note'] = 'Cloud verbunden; warte auf Druckerdaten'
        client.subscribe(f'device/{self.serial}/report', qos=0)
        with self.lock:
            self.connected = True
        client.publish(f'device/{self.serial}/request', json.dumps({'pushing': {'command': 'pushall', 'sequence_id': str(int(time.time()*1000))}}), qos=0)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        with self.lock:
            self.connected = False
            self.state['connection_status'] = 'disconnected'
            self.state['connection_note'] = 'Verbindung unterbrochen; erneuter Versuch läuft'

    def _on_message(self, client, userdata, msg):
        try:
            report = json.loads(msg.payload).get('print')
            if not isinstance(report, dict):
                return
            self._merge(report)
        except (UnicodeDecodeError, ValueError, TypeError):
            return

    def _merge(self, report):
        keys = {
            'gcode_state': 'print_state', 'mc_percent': 'progress_percent',
            'mc_remaining_time': 'remaining_minutes', 'nozzle_temper': 'nozzle_temp',
            'nozzle_target_temper': 'nozzle_target', 'bed_temper': 'bed_temp',
            'bed_target_temper': 'bed_target', 'chamber_temper': 'chamber_temp',
            'subtask_name': 'file_name', 'layer_num': 'layer', 'total_layer_num': 'total_layers',
        }
        with self.lock:
            for source, target in keys.items():
                if source in report:
                    value = report[source] if source in ('gcode_state', 'subtask_name') else _number(report[source])
                    self.state[target] = value
            if isinstance(report.get('ams'), dict):
                ams_data = report['ams']
                if 'tray_now' in ams_data:
                    self.state['ams_tray_now'] = ams_data['tray_now']
                # Cloud reports are partial deltas. Keep previous slots unless updated.
                old = {(x['unit'], x['slot']): x for x in self.state['ams']}
                for unit in ams_data.get('ams', []):
                    if not isinstance(unit, dict):
                        continue
                    try:
                        unit_id = int(unit.get('id', 0))
                    except (ValueError, TypeError):
                        continue
                    for tray in unit.get('tray', []):
                        if not isinstance(tray, dict):
                            continue
                        slot = _slot(unit, tray.get('id'))
                        if slot is None:
                            continue
                        prior = old.get((unit_id, slot), {'unit': unit_id, 'slot': slot + 1})
                        for field, key in (('tray_type', 'material'), ('tray_color', 'color_hex'), ('tray_info_idx', 'filament_id'), ('remain', 'remaining_percent'), ('tray_weight', 'weight_g')):
                            if field in tray:
                                prior[key] = tray[field]
                        old[(unit_id, slot)] = prior
                self.state['ams'] = sorted(old.values(), key=lambda x: (x['unit'], x['slot']))
            self.state['last_seen'] = datetime.now(timezone.utc).isoformat()
            self.state['connection_status'] = 'connected'
            self.state['connection_note'] = 'Live-Daten vom Drucker empfangen'

    def publish_command(self, section, command, **kwargs):
        # MQTT broker acceptance is not proof the printer executed a command.
        if section not in ('print',) or command not in ('pause', 'resume', 'stop', 'print_speed', 'gcode_line', 'project_file'):
            raise ValueError('Unzulässiger MQTT-Befehl')
        with self.lock:
            connected = self.connected
            client = self.client
        if not connected or client is None:
            raise ConnectionError('Cloud MQTT ist nicht verbunden')
        payload = {section: {'command': command, 'sequence_id': str(int(time.time()*1000)), **kwargs}}
        info = client.publish(f'device/{self.serial}/request', json.dumps(payload), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f'MQTT publish fehlgeschlagen: {info.rc}')
        return {'queued': True, 'note': 'MQTT-Befehl gesendet. Ausführung am Drucker nicht bestätigt.'}

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
        if result['last_seen']:
            try:
                ts = datetime.fromisoformat(result['last_seen']).timestamp()
                if time.time() - ts > 180:
                    result['connection_note'] = 'Verbindung besteht, aber letzte Telemetrie ist älter als 3 Minuten'
            except ValueError:
                pass
        result['configured'] = all([self.serial, self.user_id, self.token])
        result['serial_suffix'] = self.serial[-4:] if self.serial else None
        return result


monitor = CloudMonitor()
