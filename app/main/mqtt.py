import os
import paho.mqtt.client as mqtt
import time
from typing import Optional

# --- Configuration ---
# Default values used if environment variables are not set.
DEFAULT_HOST = "broker.hivemq.com" # A public test broker for demonstration
DEFAULT_PORT = 1883

def publish_mqtt_message(message: str, topic: Optional[str] = None, client_id: Optional[str] = None) -> bool:
    """
    Connects to an MQTT broker specified by environment variables and publishes
    a message to a topic, also from environment variables.

    Reads the following environment variables:
    - MQTT_BROKER_HOST (MANDATORY)
    - MQTT_TOPIC_PREFIX (OPTIONAL)
    - MQTT_PORT (Optional, defaults to 1883)

    Args:
        message: The payload string to publish.
        client_id: The unique identifier for the MQTT client. If None, one is generated.

    Returns:
        True if the message was published successfully, False otherwise.
    """
    # 1. Retrieve configuration from environment variables
    host = os.environ.get('MQTT_BROKER_HOST')
    topic_prefix = os.environ.get('MQTT_TOPIC_PREFIX')
    port = int(os.environ.get('MQTT_PORT', DEFAULT_PORT))
    user = os.environ.get('MQTT_USER')
    password = os.environ.get('MQTT_PASS')

    topic_complete = ""

    if not host:
        return None

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


    print(f"Attempting to connect to: {host}:{port}")
    print(f"Publishing to topic: {topic_complete}")

    # 2. Define callback functions (optional but good practice)

    def on_connect(client, userdata, flags, rc):
        """Called when the client receives a CONNACK response from the broker."""
        if rc == 0:
            print("Connection successful.")
        else:
            print(f"Connection failed with code {rc}. Check credentials or network.")

    def on_publish(client, userdata, mid):
        """Called when a message is successfully published."""
        print(f"Message published successfully (MID: {mid}).")

    # 3. Initialize the MQTT client
    if client_id is None:
        client_id = f'python-mqtt-publisher-{time.time_ns()}'
    client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311)

    # 4. Assign callbacks
    client.on_connect = on_connect
    client.on_publish = on_publish

    if user:
        client.username_pw_set(username=user, password=password)
        print(f"Using authentication for user: {user}")

    try:
        # 5. Connect to the broker (blocking call)
        client.connect(host, port, keepalive=60)
        client.loop_start() # Start the background thread for network communication

        # 6. Publish the message
        # We publish with QoS 1 (at least once) and retain=False
        result, mid = client.publish(topic_complete, message, qos=1, retain=False)
        print(f"Publish request status: {mqtt.error_string(result)}")

        # Wait briefly for the publish confirmation to ensure on_publish is called
        time.sleep(2)
        
        return result == mqtt.MQTT_ERR_SUCCESS

    except Exception as e:
        print(f"An error occurred during MQTT operation: {e}")
        return False
    finally:
        # 7. Disconnect and clean up resources
        client.loop_stop()
        client.disconnect()
        print("Disconnected from MQTT broker.")