import broadlink
import base64

# Broadlink device setup
BROADLINK_IP = "192.168.1.68"
BROADLINK_MAC = "E81656A1AEF2"

# Initialize Broadlink device
device = broadlink.gendevice(0x2737, (BROADLINK_IP, 80), BROADLINK_MAC)
device.auth()

# Fan commands dictionary
FAN_COMMANDS = {
            "ventilador_despacho": {
                "velocidad_3": "scAMA8SeBgAnDA4lDiUOJScNDCcnDCcNDCcMJw0nDCYNJicNDCcMJw0mDScNJiYNDSclDQ4AAX8OJQ4lJwwNJw0mDSYnDQwnJg0mDQ0mDSYNJwwnDCcnDQwnDSYNJg0mDiUnDQwnJg0NAAGADSYNJicNDSYNJg0lKAwNJicNJg0MJw0mDScNJg0mJg0NJg4lDiUNJw0mJw0MJyYODAABgg0mDSYmDA4mDiUNJicNDCcnDSUODCcNJg0mDSYNJicNDCcNJg0mDScNJiYODCYnDQ0AAX8OJQ0nJwwNJg0mDScmDQ0mJg0mDQ0mDScMJwwnDSYnDQ0mDSYNJg0mDiYmDQwnJw0MAAGADSYNJyYNDSYNJg0mJwwNJyYNJg0NJg0nDCcNJg0mJg0OJQ0mDScMJw0nJg0NJiYODAABgg0mDSYmDQ4lDSYNJycMDSYmDiUODCcNJg0mDiUNJicNDSYNJg0nDCcNJyUNDSYnDQwAAYANJw0mJwwNJwwnDSYmDQ0mJwwnDA0nDCcNJg0mDSclDg0mDSYNJg4lDScmDQ0mJw0MAAGBDCcNJiYNDSYOJQ4lJwwNJycMJwwNJwwnDSYNJg0mJg0NJwwnDSYNJwwnJg0NJiYNDQABgQ4lDiYnDA0mDSYNJycMDSYmDiUNDSYOJg0mDCcNJicNDSYNJg0nDCcNJScNDSYnDQwAAYANJg0nJwwNJg0mDScmDA4mJg0mDQwnDScMJw0mDSclDg0lDiUOJg0mDScnDA0mJw0MAAGBDCcNJiYMDiYOJQ0mJw0MJycMJwwNJwwnDSUOJg0mJw0MJwwnDSYNJw0mJg0NJiYNDgABgQ0mDiUnDA0nDSYNJiYODCcmDSYNDSYNJg0mDScMJycNDCcMJw0mDSYOJScMDScmDQ0AAYANJg0mJw0NJg0mDSYnDA4lJw0mDQwnDScMJw0mDSYmDQ4lDiUNJwwnDScmDQ0mJg4MAAGBDCcNJScMDiYNJg0mJwwNJyYNJg4MJwwmDiUOJgwnJw0MJwwnDSYNJw0mJgwOJiYNDgAF3A==",
                "off": "scCwBMSeBgAnDA4lDiUOJScNDCcnDCcNDCcMJw0mDScMJyYMDiYNJg0mDScNJg0mDScmDQ0AAYANJg0mJg0NJg4lDiUnDQwnJwwnDQwnDCcNJwwnDSYmDQ0mDiUOJQ0nDSYNJg0nJwwNAAGADSYNJicNDSYNJQ4lKAwOJScMJw0MJwwoDCcMJw0mJg4MJg4lDiYMJw0mDScNJicNDAABgQ0mDSYmDg0mDSUOJScNDiUnDSYNDCcNJwwnDCcNJiYODCYOJQ4mDSYNJg0nDCcnDQwAAYANJg0nJg0NJg0nDSUnDA4lJw0mDQwnDScMJw0mDSYmDg0lDiYNJg0mDScMJw0mJw0MAAGADScNJicMDScNJg0lJwwOJiYNJwwNJwwnDSYNJwwnJQ4NJwwmDiUOJg0mDSYNJicNDQABgA0nDSYnDA0nDCcNJiYMDiYmDScMDScMJw0mDScMJyUODScMJg0mDiYOJQ0mDSYnDQ0AAYANJg0mJw0MJw0mDSYmDQ0mJw0mDQwnDCcNJwwnDCcmDgwnDCcNJQ4mDiUNJg0nJg0NAAGADSYNJicNDSYNJg0mJg0NJicNJg0MJwwnDScMJw0mJg4MJw0mDSUOJg4lDiUNJycMDQABgQ0mDSYnDQwnDSYNJyUODCYnDSYNDSYNJwwnDCcNJicNDCcNJg0mDSYOJQ4mDCcnDQwAAYANJg0nJwwNJg0mDSclDg0mJg0mDQ0mDScMJwwnDSYnDQ0mDScMJwwmDiYNJg4lJw0NAAF/DScNJicMDSYNJw0mJg0NJyYMJwwOJgwnDSYNJwwnJg0NJwwnDSYNJg0mDiUOJScNDQABgA0mDScnDA0mDScNJiYNDSclDScMDiUOJgwnDSYNJicNDSYNJwwnDSYNJg0mDiUnDQwAAYENJg0mJwwNJw0mDSYmDgwnJg0mDA4mDSYNJg0nDCcnDA0nDCcMJw0nDCYOJQ4mJg0NAAGADiUNJicNDSYNJg0mJg4MJyYOJQ0NJg0mDiYMJw0mJw0MJw0mDSYNJw0mDSUOJiYNDQABgQ4lDSYnDA0nDSYNJicNDCcmDSYNDSYNJg4mDCcNJicNDCcMJw0mDScNJg0lDiYnDA4AAX8OJQ4mJwwNJg0mDScnDA0mJg4lDgwmDiYNJg0mDSYnDQwnDSYNJwwnDScMJg0mJw0NAAF/DiYOJScMDSYNJw0mJwwNJyYNJg0NJg0mDiUOJQ0nJg0NJwwnDCcNJwwnDSYNJScNDQABgA4mDiUnDA0mDScNJicMDSclDiUODSYNJg0mDiUNJicNDSYNJwwnDScMJwwnDSUnDQ0AAX8OJg4lJwwNJw0mDSYnDA0nJg0mDQ0nDCYOJQ4mDSYmDQ0nDCcMJw0nDCcNJg0mJg0NAAGBDSUOJScMDScNJg0mJw0MJycMJg4MJwwmDiUOJg0mJwwNJwwnDSYNJw0mDSYNJiYNDgABgQwmDiUnDA4mDSYNJicMDScnDCYNDSYNJw0lDiYNJiYNDScMJwwnDScNJg0mDSYmDg0AAYANJQ4lJw0NJg0mDSYnDQwnJwwmDgwnDCcNJg0mDSYnDQwnDCcNJg0nDSYNJg0nJQ4NAAGADSYNJQ=="
            }
        }

def send_command(fan_name, command):
    if fan_name in FAN_COMMANDS and command in FAN_COMMANDS[fan_name]:
        encoded_command = FAN_COMMANDS[fan_name][command]
        decoded_command = base64.b64decode(encoded_command)
        device.send_data(decoded_command)
        print(f"Sent {command} command to {fan_name}")
    else:
        print(f"Invalid fan name or command")

# Example usage
send_command("ventilador_despacho", "velocidad_3")
