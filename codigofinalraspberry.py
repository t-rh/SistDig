import serial
import RPi.GPIO as GPIO
import time
import json
from datetime import datetime
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

# Config general
USER_ID = "trh"          
DEVICE_ID = "berry"                      
DEVICE_CREDENTIAL = "berry123"

MQTT_BROKER = "backend.thinger.io"
MQTT_PORT = 1883

TOPIC_PUB = f"v2/{USER_ID}/devices/{DEVICE_ID}/data"

# GPIO 
MODO_GPIO = GPIO.BOARD   
PIN_BUZZER = 3         
                      
PUERTO_SERIE = "/dev/ttyACM0"  
BAUDIOS = 9600

UMBRAL_DISTANCIA = 50
TIEMPO_MAX_DISTANCIA = 20 * 60 # 20 minutos en segundos


# ESTADO DEL SISTEMA
class EstadoSistema:
    def __init__(self):
        self.hora_alarma = 12
        self.minuto_alarma = 0
        self.distancia_actual = 0
        self.amarillo_pulsado_hoy = False
        self.alarma_sonando = False
        self.modo_noche_activo = False
        self.tiempo_inicio_distancia = None
        self.alerta_distancia_enviada = False
        self.mensaje_alerta_display = "Sistema iniciado correctamente"
        self.ultimo_envio_mqtt = 0

estado = EstadoSistema()

# buzzer
def inicializar_gpio():
    GPIO.cleanup()  
    GPIO.setmode(MODO_GPIO)
    GPIO.setup(PIN_BUZZER, GPIO.OUT)
    GPIO.output(PIN_BUZZER, GPIO.LOW)
    print(f"[GPIO] Configurado en modo BOARD. Pin Buzzer asignado: {PIN_BUZZER}")

def encender_buzzer():
    GPIO.output(PIN_BUZZER, GPIO.HIGH)

def apagar_buzzer():
    GPIO.output(PIN_BUZZER, GPIO.LOW)

# prueba para asegurar que el buzzer funciona y esta bien conectado
def probar_hardware_inicial():
    print("[HARDWARE] Realizando test inicial de Buzzer (0.5s)...")
    encender_buzzer()
    time.sleep(0.5)
    apagar_buzzer()
    print("[HARDWARE] Test inicial de Buzzer completado.")

def on_disconnect(client, userdata, disconnect_flags=None, reason_code=0, properties=None):
    print(f"\n[MQTT] Desconectado del broker (reason_code={reason_code}). "
          f"paho-mqtt reintentará la conexión automáticamente...\n")

def crear_cliente_mqtt():
    client = mqtt.Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=DEVICE_ID
    )
    client.username_pw_set(USER_ID, DEVICE_CREDENTIAL)
    client.on_disconnect = on_disconnect
    # Reintentos automáticos de reconexión
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client

# Lógica del sistema y alarmasS
def evaluar_alarma_medicina(ahora):
    if ahora.hour == estado.hora_alarma and ahora.minute == estado.minuto_alarma:
        if not estado.amarillo_pulsado_hoy and not estado.alarma_sonando:
            estado.mensaje_alerta_display = f"¡ALARMA! Medicina no tomada ({estado.hora_alarma:02d}:{estado.minuto_alarma:02d})"
            print(f"\n[EVENTO ALARMA] {estado.mensaje_alerta_display}")
            encender_buzzer()
            estado.alarma_sonando = True

def evaluar_sensor_distancia():
    if estado.modo_noche_activo:
        if estado.distancia_actual > UMBRAL_DISTANCIA:
            if estado.tiempo_inicio_distancia is None:
                estado.tiempo_inicio_distancia = time.time()

            transcurrido = time.time() - estado.tiempo_inicio_distancia
            if transcurrido >= TIEMPO_MAX_DISTANCIA and not estado.alerta_distancia_enviada:
                estado.mensaje_alerta_display = f"¡ALERTA! Distancia ({estado.distancia_actual}cm) > {UMBRAL_DISTANCIA}cm"
                print(f"\n[EVENTO DISTANCIA] {estado.mensaje_alerta_display}")
                encender_buzzer()
                estado.alerta_distancia_enviada = True
        else:
            estado.tiempo_inicio_distancia = None
            if estado.alerta_distancia_enviada:
                estado.alerta_distancia_enviada = False
                if not estado.alarma_sonando:
                    apagar_buzzer()

def procesar_mensajes_arduino(linea):
    print(f"[SERIE RAW] Arduino -> '{linea}'")

    if linea == "AMARILLO_PULSADO":
        estado.amarillo_pulsado_hoy = True
        estado.mensaje_alerta_display = "Medicina tomada correctamente"
        print(" -> [EVENTO] Botón Amarillo Pulsado (Medicina tomada)")
        if estado.alarma_sonando:
            apagar_buzzer()
            estado.alarma_sonando = False

    elif linea == "ROJO_PULSADO":
        estado.modo_noche_activo = not estado.modo_noche_activo
        estado_str = "ACTIVADO" if estado.modo_noche_activo else "DESACTIVADO"
        estado.mensaje_alerta_display = f"Modo distancia: {estado_str}"
        print(f" -> [EVENTO] Botón Rojo Pulsado (Modo distancia: {estado_str})")

        if not estado.modo_noche_activo:
            estado.tiempo_inicio_distancia = None
            if estado.alerta_distancia_enviada:
                estado.alerta_distancia_enviada = False
                if not estado.alarma_sonando:
                    apagar_buzzer()

    elif linea.startswith("DIST:"):
        try:
            estado.distancia_actual = int(linea.split(':')[1])
        except (IndexError, ValueError):
            pass

def enviar_telemetria_a_thinger(client, ahora):
    telemetria = {
        "distancia_cm": estado.distancia_actual,
        "medicina_tomada": estado.amarillo_pulsado_hoy,
        "modo_noche": estado.modo_noche_activo,
        "alarma_activa": estado.alarma_sonando,
        "mensaje_estado": estado.mensaje_alerta_display
    }

    payload_json = json.dumps(telemetria)

    print("\n" + "=" * 65)
    print("[LOG DE ESTADO - RASPBERRY PI]")
    print(f"  * Hora actual Raspberry:    {ahora.strftime('%H:%M:%S')}")
    print(f"  * Hora alarma programada:  {estado.hora_alarma:02d}:{estado.minuto_alarma:02d}")
    print(f"  * Distancia medida:       {estado.distancia_actual} cm")
    print(f"  * Medicina tomada hoy:     {estado.amarillo_pulsado_hoy}")
    print(f"  * Estado Buzzer (PIN):     {'HIGH (ENCENDIDO)' if estado.alarma_sonando or estado.alerta_distancia_enviada else 'LOW (APAGADO)'}")
    print(f"  * Mensaje de Estado:       '{estado.mensaje_alerta_display}'")
    print("=" * 65 + "\n")

    client.publish(TOPIC_PUB, payload_json)
    estado.ultimo_envio_mqtt = time.time()

# Main
def main():
    print("=== INICIO RASPBERRY PI ===")

    # 1. Inicializar Hardware y Test
    inicializar_gpio()
    probar_hardware_inicial()

    # 2. Configurar y Conectar MQTT
    mqtt_client = crear_cliente_mqtt()
    mqtt_client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=60)
    mqtt_client.loop_start()

    # 3. Conexión con Arduino
    arduino = None

    # 4. Bucle Principal de Ejecución
    try:
        while True:
            # Reintento de conexión serie si se desconecta
            if arduino is None or not arduino.is_open:
                try:
                    arduino = serial.Serial(PUERTO_SERIE, BAUDIOS, timeout=1)
                    time.sleep(2)
                    print(f"[SERIE] ¡Arduino conectado en {PUERTO_SERIE}!")
                except serial.SerialException:
                    time.sleep(2)

            ahora = datetime.now()

            # Reset diario a medianoche
            if ahora.hour == 0 and ahora.minute == 0 and ahora.second == 0:
                estado.amarillo_pulsado_hoy = False

            # Evaluación de eventos
            evaluar_alarma_medicina(ahora)
            evaluar_sensor_distancia()

            # Lectura del puerto serie de Arduino
            if arduino and arduino.is_open and arduino.in_waiting > 0:
                linea = arduino.readline().decode('utf-8', errors='ignore').strip()
                if linea:
                    procesar_mensajes_arduino(linea)

            # Publicación periódica cada 5 segundos
            if time.time() - estado.ultimo_envio_mqtt >= 5:
                enviar_telemetria_a_thinger(mqtt_client, ahora)

            time.sleep(0.05)

    except KeyboardInterrupt:
        print("\n[SISTEMA] Detención solicitada por el usuario.")

    finally:
        print("[SISTEMA] Cerrando conexiones y liberando pines...")
        apagar_buzzer()
        GPIO.cleanup()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        if arduino and arduino.is_open:
            arduino.close()
        print("[SISTEMA] Programa finalizado correctamente.")



if __name__ == "__main__":
    main()
