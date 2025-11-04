import os
import paho.mqtt.client as mqtt
import json
import time
import subprocess
from typing import Optional
from .config import base_path

from .routes_frontend import get_pico_recipes
from .session_parser import (active_brew_sessions, active_ferm_sessions,
                             active_iSpindel_sessions, active_tilt_sessions, active_still_sessions)
from .config import base_path

# --- Configuration ---
DEFAULT_PORT = 1883

def parse_and_send(itype: int, uid: str, message: str, topic: Optional[str] = None, client_id: Optional[str] = None) -> bool:
    '''
    'Type' Definitions:
    0: Undefined
    1: Registration
    2: Get Recipe
    3: Log
    4: Change State
    5: Get Actions Needed
    6: Error
    '''
    HA_ENABLED = os.environ.get('HOMEASSISTANT')
    topic_prefix = os.environ.get('MQTT_TOPIC_PREFIX')

    topic_complete = ""

    if HA_ENABLED:
        if itype == 1:
            gitSha = subprocess.check_output("cd {}; git rev-parse --short HEAD".format(base_path()), shell=True)
            gitSha = gitSha.decode("utf-8")

            # print(server_config())
            machineFound = False
            if (uid in {**active_brew_sessions, **active_ferm_sessions, **active_iSpindel_sessions, **active_tilt_sessions, **active_still_sessions}): #and active_session(uid).alias != ''
                machineFound = True
                print(active_brew_sessions[uid].alias)
                print(active_brew_sessions[uid].machine_type)

            msg = {
                "dev": {
                    "ids": uid,
                    "name": (active_brew_sessions[uid].alias) if (machineFound) else ("PicoBrew"),
                    "mf": "PicoBrew",
                    "mdl": (active_brew_sessions[uid].machine_type) if (machineFound) else ("UNKNOWN"),
                    "sw": "0.1.34",
                    # "sn": "ea334450945afc",
                    # "hw": "1.0rev2"
                },
                "o": {
                    "name":"picobrew_pico",
                    "sw": gitSha,
                    # "url": "https://bla2mqtt.example.com/support"
                },
                "cmps": {
                    ("pico_" + str(uid) + "_state"): {
                        "name": "Operational State",
                        "p": "sensor",
                        "value_template":"{{ value_json.state}}",
                        "unique_id": "pico_" + str(uid) + "_state",
                        "icon": "mdi:beer"
                    },
                    ("pico_" + str(uid) + "_recipe"): {
                        "name": "Recipe",
                        "p": "sensor",
                        "value_template":"{{ value_json.recipe}}",
                        "unique_id": "pico_" + str(uid) + "_recipe",
                        "icon": "mdi:format-list-numbered"
                    },
                    ("pico_" + str(uid) + "_sesId"): {
                        "name": "Session ID",
                        "p": "sensor",
                        "value_template":"{{ value_json.sesId}}",
                        "unique_id": "pico_" + str(uid) + "_sesId",
                        "icon": "mdi:format-list-numbered"
                    },
                    ("pico_" + str(uid) + "_sesType"): {
                        "name": "Session Type",
                        "p": "sensor",
                        "value_template":"{{ value_json.sesType}}",
                        "unique_id": "pico_" + str(uid) + "_sesType",
                        "icon": "mdi:beer"
                    },
                    ("pico_" + str(uid) + "_step"): {
                        "name": "Session Step",
                        "p": "sensor",
                        "value_template":"{{ value_json.step}}",
                        "unique_id": "pico_" + str(uid) + "_step",
                        "icon": "mdi:counter"
                    },
                    ("pico_" + str(uid) + "_error"): {
                        "name": "Error Code",
                        "p": "sensor",
                        "value_template":"{{ value_json.error}}",
                        "unique_id": "pico_" + str(uid) + "_error",
                        "icon": "mdi:alert-circle-outline"
                    },
                    ("pico_" + str(uid) + "_wortTemp"): {
                        "name": "Wort Temperature",
                        "p": "sensor",
                        "device_class":"temperature",
                        "unit_of_measurement":"°F",
                        "value_template":"{{ value_json.wort}}",
                        "unique_id": "pico_" + str(uid) + "_wortTemp",
                        "icon": "mdi:water-thermometer"
                    },
                    ("pico_" + str(uid) + "_blockTemp"): {
                        "name": "Heat Block Temperature",
                        "p": "sensor",
                        "device_class":"temperature",
                        "unit_of_measurement":"°F",
                        "value_template":"{{ value_json.therm}}",
                        "unique_id": "pico_" + str(uid) + "_blockTemp",
                        "icon": "mdi:thermometer-high"
                    },
                    ("pico_" + str(uid) + "_timeLeft"): {
                        "name": "Time Remaining",
                        "p": "sensor",
                        "value_template":"{{ value_json.timeLeft}}",
                        "unique_id": "pico_" + str(uid) + "_timeLeft",
                        "unit_of_measurement": "s",
                        "device_class": "duration",
                        "icon": "mdi:clock-check"
                    }
                },
                "state_topic":"homeassistant/pico/" + str(uid) + "/state",
                "qos": 2
            }
            publish_mqtt_message(json.dumps(msg), 'homeassistant/device/pico/' + str(uid) + '/config')
        else:
            topic_complete = "homeassistant/pico/" + str(uid) + "/state"
            messageJSON = json.loads(message)
            bodyJSON = {}
            if itype == 2:
                if 'rfid' in messageJSON:
                    recipe = next((r for r in get_pico_recipes(False) if r.id == messageJSON['rfid']), None)
                    bodyJSON['recipe'] = ('Invalid Recipe') if not recipe else (recipe.name)
                publish_mqtt_message(json.dumps(bodyJSON), topic_complete)
            if itype == 3:
                if 'state' in messageJSON:
                    # 2 = Ready, 3 = Brewing, 4 = Sous Vide, 5 = Rack Beer, 6 = Rinse, 7 = Deep Clean, 9 = De-Scale
                    if messageJSON['state'] == 2:
                        bodyJSON['state'] = "Ready"
                    elif messageJSON['state'] == 3:
                        bodyJSON['state'] = "Brewing"
                    elif messageJSON['state'] == 4:
                        bodyJSON['state'] = "Sous Vide"
                    elif messageJSON['state'] == 5:
                        bodyJSON['state'] = "Rack Beer"
                    elif messageJSON['state'] == 6:
                        bodyJSON['state'] = "Rinse"
                    elif messageJSON['state'] == 7:
                        bodyJSON['state'] = "Deep Clean"
                    elif messageJSON['state'] == 9:
                        bodyJSON['state'] = "De-Scale"
                    else:
                        bodyJSON['state'] = "Unknown"
                else:
                    bodyJSON['state'] = "Brewing"
                if 'wort' in messageJSON:
                    bodyJSON['wort'] = messageJSON['wort']
                if 'therm' in messageJSON:
                    bodyJSON['therm'] = messageJSON['therm']
                if 'sesId' in messageJSON:
                    bodyJSON['sesId'] = messageJSON['sesId']
                if 'sesType' in messageJSON:
                    # 0 = Brewing, 1 = Deep Clean, 2 = Sous Vide
                    if messageJSON['sesType'] == 0:
                        bodyJSON['sesType'] = "Brewing"
                    elif messageJSON['sesType'] == 1:
                        bodyJSON['sesType'] = "Deep Clean"
                    elif messageJSON['sesType'] == 2:
                        bodyJSON['sesType'] = "Sous Vide"
                    else:
                        bodyJSON['sesType'] = "Unknown"
                if 'step' in messageJSON:
                    bodyJSON['step'] = messageJSON['step'].capitalize()
                if 'error' in messageJSON:
                    bodyJSON['error'] = messageJSON['error']
                if 'timeLeft' in messageJSON:
                    bodyJSON['timeLeft'] = messageJSON['timeLeft']
                publish_mqtt_message(json.dumps(bodyJSON), topic_complete)
            if itype == 4:
                if 'state' in messageJSON:
                    # 2 = Ready, 3 = Brewing, 4 = Sous Vide, 5 = Rack Beer, 6 = Rinse, 7 = Deep Clean, 9 = De-Scale
                    if messageJSON['state'] == 2:
                        bodyJSON['state'] = "Ready"
                    elif messageJSON['state'] == 3:
                        bodyJSON['state'] = "Brewing"
                    elif messageJSON['state'] == 4:
                        bodyJSON['state'] = "Sous Vide"
                    elif messageJSON['state'] == 5:
                        bodyJSON['state'] = "Rack Beer"
                    elif messageJSON['state'] == 6:
                        bodyJSON['state'] = "Rinse"
                    elif messageJSON['state'] == 7:
                        bodyJSON['state'] = "Deep Clean"
                    elif messageJSON['state'] == 9:
                        bodyJSON['state'] = "De-Scale"
                    else:
                        bodyJSON['state'] = "Unknown"
                else:
                    bodyJSON['state'] = "Unknown"
            if itype == 5:
                pass
            if itype == 6:
                pass
    else:
        if not topic:
            if not topic_prefix:
                print("INFO: MQTT Topic not specified and MQTT_TOPIC_PREFIX environment variable is not set.")
                topic_complete = topic_complete + "PICO"
            else:
                print("INFO: MQTT Topic not specified, but MQTT_TOPIC_PREFIX was specified via ENV variable")
                topic_complete = topic_complete + topic_prefix
        else:
            if not topic_prefix:
                print("INFO: MQTT Topic was specified, but MQTT_TOPIC_PREFIX was not set")
                topic_complete = topic_complete + topic
            else:
                topic_complete = topic_complete + topic_prefix + "/" + topic
        publish_mqtt_message(json.dumps(message), topic_complete, client_id)




def publish_mqtt_message(message: str, topic: str, client_id: Optional[str] = None) -> bool:
    host = os.environ.get('MQTT_BROKER_HOST')
    port = int(os.environ.get('MQTT_PORT', DEFAULT_PORT))
    user = os.environ.get('MQTT_USER')
    password = os.environ.get('MQTT_PASS')

    if not host:
        return None

    print(f"Attempting to connect to: {host}:{port}")
    print(f"Publishing to topic: {topic}")

    def on_connect(client, userdata, flags, rc):
        """Called when the client receives a CONNACK response from the broker."""
        if rc == 0:
            print("Connection successful.")
        else:
            print(f"Connection failed with code {rc}. Check credentials or network.")

    def on_publish(client, userdata, mid):
        """Called when a message is successfully published."""
        print(f"Message published successfully (MID: {mid}).")

    if client_id is None:
        client_id = f'python-mqtt-publisher-{time.time_ns()}'
    client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)

    client.on_connect = on_connect
    client.on_publish = on_publish

    if user:
        client.username_pw_set(username=user, password=password)
        print(f"Using authentication for user: {user}")

    try:
        client.connect(host, port, keepalive=60)
        client.loop_start()

        result, mid = client.publish(topic, str(message), qos=1, retain=False)
        print(f"Publish request status: {mqtt.error_string(result)}")

        # # Wait briefly for the publish confirmation to ensure on_publish is called
        # time.sleep(0.5)
        
        return result == mqtt.MQTT_ERR_SUCCESS

    except Exception as e:
        print(f"An error occurred during MQTT operation: {e}")
        return False
    finally:
        client.loop_stop()
        client.disconnect()
        print("Disconnected from MQTT broker.")

