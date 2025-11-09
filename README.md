# picobrew_pico
![GitHub contributors](https://img.shields.io/github/contributors/wrcrooks/picobrew_pico)
![GitHub commit activity](https://img.shields.io/github/commit-activity/m/wrcrooks/picobrew_pico)

![Docker Image Version (latest server)](https://img.shields.io/docker/v/wrcrooks/picobrew_pico?logo=docker)
![Docker Pulls](https://img.shields.io/docker/pulls/wrcrooks/picobrew_pico?logo=docker)

**NOTE:** *This project is under active development. Please ensure you have the Docker volumes bound properly to prevent data loss while upgrading versions and/or switching between this image and the chiefwigms image*

This is a modified version of chiefwigms' [picobrew_pico](https://github.com/chiefwigms/picobrew_pico) project, with added Home Assistant/MQTT Support. Allows for full local control of the PicoBrew Pico S/C/Pro & Zymatic models.

## Supported Devices:
* Hot Side
  * Pico S: Fully featured ![TESTED](https://img.shields.io/badge/TESTED-green)
  * Pico C: Fully featured ![TESTED](https://img.shields.io/badge/TESTED-green)
  * Pico Pro: Fully featured ![TESTED](https://img.shields.io/badge/TESTED-green)
  * Zymatic: Fully featured
  * ZSeries: Fully featured
  * PicoStill: Fully featured
    * Optional internal PicoStill T1/2/3/4 and Pressure Logging 
    * Firmware versions 0.0.30 - 0.0.35 (selectable)
    * PicoBrew Controlling Devices:
      * Pico S/C/Pro distillation (no heat sensor logging - limitation of firmware)
      * ZSeries (full heat sensor logging - included and supported within Z firmware)
      * Zymatic (no native support for controlling the PicoStill - limitation of firmware)
* Cold Side (Fermentation)
  * PicoFerm (Beta - Currently terminates fermentation after 14 days)
  * iSpindel: Full session graphing
  * Tilt: Full session graphing

## Features
#### Implemented
* Home Assistant Integration ![Home Assistant](https://img.shields.io/badge/Home%20Assistant-white?logo=homeassistant
)
* Device Aliasing
* Brew Sessions
  * Live Graphing
  * Historical Graphing
* Recipe Library
  * View Previously Created
  * Create New Recipes
  * Import from PicoBrew Servers (Pico C/S/Pro and Zymatic)
* Manual Recipe Editing
  * **Note** The table for adding/removing/editing recipe steps has several validation checks in it, but there is always the possibility of ruining your Pico.  
  * *For Pico S/C/Pro Only*: DO NOT EDIT or MOVE Rows 1-3 (Preparing to Brew/Heating/Dough In).  Drain times should all be 0 except for Mash Out (2 minutes) and the last hop addition (5 minutes) (for example, if you only have Hops 1 & 2, set the drain time on Hops 2 to 5, and remove the Hops 3 and 4 rows)

#### Work In Progress
* PicoBrew inspired "Recipe Builder" (AIO, with support for all devices)
* Ingredients Library, for use with the aforementioned Recipe Builder

# Installation
After some consideration, I have decided to only develop with the creation of a Docker image in mind. While I realize this requires some knowledge of containers to use, I ultimately believe this is the easiest way to install and manage for the end user, with platform-agnostic compatibility being a bonus.

## Requirements
DNS Override (either through a router, RaspberryPi etc)  
  - Your router needs to override DNS queries to `picobrew.com` with the local, private IP of the container. Guides:
    - DD-WRT/Open-WRT etc : Add additional option added to dnsmasq.conf: `address=/picobrew.com/<Server IP running this code>`

### Running pre-packaged server via Docker or Docker-Compose ![Docker Image Version (latest server)](https://img.shields.io/docker/v/wrcrooks/picobrew_pico?logo=docker)

Docker v27.X (https://docs.docker.com/get-docker/)

#### Setup/Run

##### Step 1: Create directory structure
Set up the following directory structure for use by the server:
```
picobrew/
  app/
    recipes/
      pico/
        archive/
      zseries/
        archive/
      zymatic/
        archive/
      unified/
        archive/
      ingredients/
    sessions/
      brew/
        active/
        archive/
      ferm/
        active/
        archive/
      iSpindel/
        active/
        archive/
      still/
        active/
        archive/
      tilt/
        active/
        archive/
  nginx/
    certs/
    conf/
    scripts/
```

Commands to run on Docker host:
```
cd <Host Docker Data Directory>
mkdir -p picobrew/app/recipes/pico/archive picobrew/app/recipes/zseries/archive picobrew/app/recipes/zymatic/archive picobrew/app/recipes/unified/archive picobrew/app/recipes/ingredients picobrew/app/sessions/brew/active picobrew/app/sessions/brew/archive picobrew/app/sessions/ferm/active picobrew/app/sessions/ferm/archive picobrew/app/sessions/iSpindel/active picobrew/app/sessions/iSpindel/archive picobrew/app/sessions/still/active picobrew/app/sessions/still/archive picobrew/app/sessions/tilt/active picobrew/app/sessions/tilt/archive picobrew/nginx/conf picobrew/nginx/certs picobrew/nginx/scripts
```

Run server volume mounting the above directory structure. Below are examples for use of these directories when running the app. More on this later.
  - Docker Compose (example snippet only):
    ```
    volumes:
      - <Host Docker Data Directory>/picobrew/nginx/conf:/etc/nginx/conf.d
      - <Host Docker Data Directory>/picobrew/nginx/certs:/certs
      - <Host Docker Data Directory>/picobrew/nginx/scripts:/scripts
    ...
    volumes:
      - <Host Docker Data Directory>/picobrew/app/recipes:/picobrew_pico/app/recipes
      - <Host Docker Data Directory>/picobrew/app/sessions:/picobrew_pico/app/sessions
      - <Host Docker Data Directory>/picobrew/app/firmware:/picobrew_pico/app/firmware
      - <Host Docker Data Directory>/picobrew/config.yaml:/picobrew_pico/config.yaml
    ```
  - Docker Command (example only):
    ```
    docker run -d -it -p 80:80 --name picobrew_pico \
    --mount type=bind,source=<Host Docker Data Directory>/picobrew/app/recipes,target=/picobrew_pico/app/recipes \
    --mount type=bind,source=<Host Docker Data Directory>/picobrew/app/sessions,target=/picobrew_pico/app/sessions \
    --mount type=bind,source=<Host Docker Data Directory>/picobrew/app/firmware,target=/picobrew_pico/app/firmware \
    --mount type=bind,source=<Host Docker Data Directory>/picobrew/config.yaml,target=/picobrew_pico/config.yaml \
    wrcrooks/picobrew_pico
    ```

##### Step 2: Generate SSL Certs (Optional but *highly* recommended)
![RECOMMENDED](https://img.shields.io/badge/RECOMMENDED-green)
If you are looking to support a ZSeries device which requires HTTP+SSL communication we need to generate some self-signed certificates to place in front of the flask app. These will be used when running nginx to terminate SSL connection before sending the requests for processing by flask.

1. Ensure you have OpenSSL installed on the Docker host machine (Example for Debian: `sudo apt install openssl`)
2. On Docker host machine, run the following commands:
  - `cd <Host Docker Data Directory>/picobrew/nginx`
  - `sudo su` *(if not running as root)*
  - `wget -qO- https://raw.githubusercontent.com/wrcrooks/picobrew_pico/refs/heads/master/scripts/docker/nginx/ssl_certificates.sh | bash`
    - **NOTE:** The intent of this script is to generate the required SSL certificates that NGINX needs to emulate the picobrew.com certificate. Generally speaking, it's bad practice to execute bash scripts directly from the internet. I'd recommend reviewing [the script](https://raw.githubusercontent.com/wrcrooks/picobrew_pico/refs/heads/master/scripts/docker/nginx/ssl_certificates.sh) first to confirm the intent and commands being executed

##### Step 3: Start Docker containers

Either: 
* provide all variables to docker command directly
* use the repository's docker-compose.yml (which will also include a working SSL enabled nginx configuration given you have setup certificates correctly with `./scripts/docker/nginx/ssl_certificates.sh`)
* use the repository's docker-compose-no-ssl.yml for a non-SSL intall (this should work for non ZSeries devices)

###### Environment Variables:
All of these variables are optional, but essential to the Home Assistant integration working.
| ENV Variable | Value | Notes |
| ------------ | ----- | ----- |
| MQTT_BROKER_HOST | \<MQTT Server IP\> |
| MQTT_PORT | 1883 |
| MQTT_TOPIC_PREFIX | \<pico\> |
| MQTT_USER | \<MQTT Username\> |
| MQTT_PASS | \<MQTT Password\> |
| HOMEASSISTANT | True | *Overrides 'MQTT_TOPIC_PREFIX'* |

To add these variables to the Docker Compose method, modify your docker-compose.yml file under the "environment" of the "app" container. Example:
```
    environment:
      FLASK_ENV: development
      PORT: 8080
      MQTT_BROKER_HOST: 192.168.1.100
      MQTT_PORT: 1883
      MQTT_TOPIC_PREFIX: pico
      MQTT_USER: mqtt
      MQTT_PASS: supersecretpassword
      HOMEASSISTANT: True
```

To add these variables to the Docker Run method, add them to your command using the `-e` flag. Example:
```
docker run -d -it -p 80:80 --name picobrew_pico \
--mount type=bind,source=<Host Docker Data Directory>/picobrew/app/recipes,target=/picobrew_pico/app/recipes \
--mount type=bind,source=<Host Docker Data Directory>/picobrew/app/sessions,target=/picobrew_pico/app/sessions \
--mount type=bind,source=<Host Docker Data Directory>/picobrew/app/firmware,target=/picobrew_pico/app/firmware \
--mount type=bind,source=<Host Docker Data Directory>/picobrew/config.yaml,target=/picobrew_pico/config.yaml \
-e MQTT_BROKER_HOST="192.168.1.100" -e MQTT_PORT="1883" -e MQTT_TOPIC_PREFIX="pico" -e MQTT_USER="mqtt" -e MQTT_PASS="supersecretpassword" -e HOMEASSISTANT="True" \
wrcrooks/picobrew_pico:latest
```

###### Option 1: Docker Compose (with SSL support via a dedicated nginx container)
![RECOMMENDED](https://img.shields.io/badge/RECOMMENDED-green)
To run a setup with http and https and want to have the ssl termination handled by the included nginx `docker-compose` is the easiest configuration to go with.

```
docker-compose up --build
```

or to start the servers in the background

```
docker-compose up --build -d
```

To view logs use the aliases service name `app` to view logs via the docker-compose command.

```
docker-compose logs -f app
```

###### Option 2: Docker Run

Running straight with docker is useful for easy setups which don't require SSL connections (aka non ZSeries brew setups) and/or for those that leveraging another existing system to handle the SSL connections (ie. mitmproxy, nginx, etc).

```
docker run -d -it -p 80:80 --name picobrew_pico \
--mount type=bind,source=<Host Docker Data Directory>/picobrew/app/recipes,target=/picobrew_pico/app/recipes \
--mount type=bind,source=<Host Docker Data Directory>/picobrew/app/sessions,target=/picobrew_pico/app/sessions \
--mount type=bind,source=<Host Docker Data Directory>/picobrew/app/firmware,target=/picobrew_pico/app/firmware \
--mount type=bind,source=<Host Docker Data Directory>/picobrew/config.yaml,target=/picobrew_pico/config.yaml \
wrcrooks/picobrew_pico
```

To view logs check the running docker containers and tail the specific instance's logs directly via docker.

```
docker ps
CONTAINER ID        IMAGE                     COMMAND                  CREATED             STATUS              PORTS                NAMES
3cfda85cd90c        wrcrooks/picobrew_pico    "/bin/sh -c 'python3…"   45 seconds ago      Up 45 seconds       0.0.0.0:80->80/tcp   picobrew_pico
```

```
docker logs -f 3cfda85cd90c
WebSocket transport not available. Install eventlet or gevent and gevent-websocket for improved performance.
 * Serving Flask app "app" (lazy loading)
 * Environment: production
   WARNING: This is a development server. Do not use it in a production deployment.
   Use a production WSGI server instead.
 * Debug mode: off
 * Running on http://0.0.0.0:80/ (Press CTRL+C to quit)
```

### Option 3: Running server via Python directly [Development only] (optionally terminating ssl elsewhere manually)
![ADVANCED](https://img.shields.io/badge/ADVANCED-grey)

Requirements:
- Python >= 3.6.9  

#### Setup/Run
Clone this repo, then run  
`sudo pip3 install -r requirements.txt` on *nix or `pip3 install -r requirements.txt` as an Administrator in windows  
`sudo python3 server.py` on *nix or `python3 server.py` as an Administrator in windows (default host interface is `0.0.0.0` and port `80`, but these can be specified via command-line arguments like so `python3 server.py <interface> <port>`)

## Home Assistant
Home Assistant support relies on having the MQTT itegration working within your HA setup, because this code utilizes the [MQTT Device Discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery) process.
#### Setup
1. Ensure that you have the MQTT integration installed on Home Assistant: [Installation](https://www.home-assistant.io/integrations/mqtt/#configuration)
2. Set up a new MQTT Broker or use and existing one: [Installation](https://www.home-assistant.io/integrations/mqtt/#setting-up-a-broker)
3. Write down your MQTT Broker settings
    * IP Address
    * Port (Default is 1883)
    * Broker Username (if any)
    * Broker Password (if any)
4. Ensure your Docker image has the following environment variables set
    * `HOMEASSISTANT: True` *REQUIRED*
    * `MQTT_BROKER_HOST: <IP From Above>` *REQUIRED*
    * `MQTT_PORT: <Port From Above>` *Not required; Defaults to 1883*
    * `MQTT_USER: <Username From Above>` *Not required; Specify only if a username is being used*
    * `MQTT_PASS: <Password From Above>` *Not required; Specify only if a password is being used*
  These env variables can be set if using the Docker command with the `-e` flag (See the **"Environment Variables"** section above)
5. When a new device connects to this PicoBrew server, a discovery message will be sent to Home Assistant via MQTT and show up in the `Settings > Devices & services > MQTT > Devices` page in Home Assistant

## Disclaimer
Except as represented in this agreement, all work product by Developer is provided ​“AS IS”. Other than as provided in this agreement, Developer makes no other warranties, express or implied, and hereby disclaims all implied warranties, including any warranty of merchantability and warranty of fitness for a particular purpose.

## Debugging
1. `docker ps` and grab the "CONTAINER ID" from the "picobrew_pico" container
2. `docker logs -f <CONTAINER ID>`

## Running Development Builds (Not Recommended)
There is a development image that gets very little unit testing: `wrcrooks/picobrew_pico:dev`
It's almost guaranteed that some features may be broken in this Docker image. You have been warned

# Hardware
## Pico C/S/Pro
* [DIY Step Filter Tray](https://www.ebay.com/itm/146917265375)
* [3D Printed Front Drain Grille](https://www.printables.com/model/1473610-picobrew-drain-grille)