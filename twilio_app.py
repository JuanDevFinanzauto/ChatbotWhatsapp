from flask import Flask, request
from twilio.rest import Client
import os
import pandas as pd
from dotenv import load_dotenv
import json
from datetime import datetime
from funciones import process_chat
from funciones_sac import process_chat_sac
import threading
import time

# Cargar variables de entorno
load_dotenv()

# Init the Flask App
app = Flask(__name__)

# Twilio credentials (should be set as environment variables in a real application)
account_sid = os.getenv('TWILIO_ACCOUNT_SID', 'ACbd615a3a6babaf0290b7415509bc430a')
auth_token = os.getenv('TWILIO_AUTH_TOKEN', os.environ['TWILIO_AUTH_TOKEN'])
client = Client(account_sid, auth_token)

TWILIO_WHATSAPP_NUMBER = 'whatsapp:+573102367623'

# Cargar la base de cobranza
base_cobranza = pd.read_excel("Base_cobranza.xlsx")

# Simple cache to store the step, credit, and state history for each user
cache = {}
cache_sac = {}

# Función para guardar las conversaciones en un CSV
def guardar_conversacion(numero, mensaje, origen):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    numero = str(numero).replace("whatsapp:+57", "")
    nuevo_registro = pd.DataFrame([[numero, mensaje, origen, timestamp]], columns=['Numero', 'Mensaje', 'Origen', 'Timestamp'])
    
    # Guardar en el archivo CSV
    nuevo_registro.to_csv('conversaciones.csv', mode='a', header=not os.path.exists('conversaciones.csv'), index=False, sep=";")

def get_cache_key(numero):
    return f"{numero}_step"

def primer_mensaje(credito, numero, twilio_num, nombre):
    palabras = nombre.lower().title().split()
    primera_palabra = palabras[0]
    segunda_palabra_desde_el_final = palabras[-2]

    nombre_inicial = primera_palabra + " " + segunda_palabra_desde_el_final

    message = client.messages.create(
        to=numero,
        content_variables=json.dumps({"1": nombre_inicial, "2": "https://www.finanzauto.com.co/Files/Politics/Politica_Proteccion_Datos_Personales.pdf"}),
        messaging_service_sid="MG05233251cb7e151e49146ac6eaad87fc",
        content_sid="HX1ef0e80952b371beb9cf6be2b2c441ef",
    )

    # Guardar el crédito y el historial de estados en el caché usando el número del cliente como clave
    cache[get_cache_key(numero)] = {
        'step': 'es_cliente',
        'credito': credito,
        'last_message_time': datetime.now(),
        'timeout_count': 0,  # Agrega un contador de intentos de timeout
        'state_history': []  # Lista de estados
    }
    guardar_conversacion(numero, message.body, 'chatbot')

    # Iniciar el temporizador de 10 segundos para cada usuario
    start_timer(numero)

def start_timer(numero):
    timer = threading.Timer(10, check_timeout, args=(numero,))  # 10 segundos
    timer.start()

def check_timeout(numero):
    cache_key = get_cache_key(numero)
    cache_data = cache.get(cache_key)
    if cache_data:
        elapsed_time = (datetime.now() - cache_data['last_message_time']).total_seconds()

        # Si ha transcurrido más de 10 segundos y no ha habido respuesta:
        if elapsed_time >= 60*5:
            cache_data['timeout_count'] += 1
            
            if cache_data['timeout_count'] == 1:
                # Cambia el estado a "esperando"
                cache_data['step'] = 'esperando'
                cache_data['state_history'].append('esperando')  # Añade el estado a la lista de historial

                # Envía un mensaje preguntando si sigue ahí
                client.messages.create(
                    body="¡Hola! 😊 Solo quería confirmar si sigues por aquí. ¿Podemos continuar?",
                    from_=TWILIO_WHATSAPP_NUMBER,
                    to=numero
                )
                cache_data['last_message_time'] = datetime.now()  # Reinicia el contador de tiempo
                start_timer(numero)  # Reinicia el temporizador
            elif cache_data['timeout_count'] == 2:
                # Termina el chat después de dos intentos
                client.messages.create(
                    body="El chat ha llegado a su fin por ahora. ¡Muchas gracias por tu tiempo! 😊",
                    from_=TWILIO_WHATSAPP_NUMBER,
                    to=numero
                )
                cache.pop(cache_key, None)  # Elimina al usuario del caché
            else:
                cache.pop(cache_key, None)  # Elimina al usuario del caché
        else:
            # Si no ha pasado 10 segundos, no hace nada y deja que el temporizador siga corriendo
            start_timer(numero)

# Define a route to handle incoming requests
@app.route('/cobranza', methods=['POST'])
def cobranza():
    incoming_msg = request.values.get('Body', '').lower()
    from_number = request.values.get('From', '')

    guardar_conversacion(from_number, incoming_msg, 'cliente')

    cache_key = get_cache_key(from_number)
    cache_data = cache.get(cache_key, {'step': 'es_cliente', 'credito': None, 'last_message_time': datetime.now(), 'timeout_count': 0, 'state_history': []})
    step = cache_data['step']
    credito = cache_data['credito']

    if cache_data['credito'] is None:
        # Uso de un cache separado para el chat SAC
        cache_key_sac = f"{from_number}_sac"
        cache_sac.setdefault(cache_key_sac, {'context': []})
        
        # Procesa el mensaje del cliente en el contexto SAC
        cache_sac[cache_key_sac]['context'].append(f"user:{incoming_msg}")
        response_message = process_chat_sac(incoming_msg, from_number,cache_sac[cache_key_sac]['context'])
        cache_sac[cache_key_sac]['context'].append(f"chatbot:{response_message}")
        guardar_conversacion(from_number, response_message, 'chatbot')

        client.messages.create(
            body=response_message,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=from_number
        )
        return '', 204

    # Actualiza la hora del último mensaje del cliente
    cache_data['last_message_time'] = datetime.now()

    if step == "esperando":
        # Regresa dos estados en la lista de estados si el cliente responde mientras está en "esperando"
        if cache_data['state_history'][-2] == "fecha_monto":

            previous_step = cache_data['state_history'][-2]
            unique_states = list(set(cache_data['state_history']))
            step = unique_states[-2]
            cache_data['state_history'].append(step)

        elif len(cache_data['state_history']) >= 3:
            previous_step = cache_data['state_history'][-2]  # Tercer paso desde el final
            step = previous_step
            cache_data['state_history'].append(step)
        else:
            step = 'es_cliente'  # Si hay menos de 3 pasos, regresa al estado inicial

    else:
        # Añade el estado actual a la lista de estados
        cache_data['state_history'].append(step)

    response_message, next_step = process_chat(credito, incoming_msg, step=step)

    if step != "no_mensaje":
        cache[cache_key] = {'step': next_step, 'credito': credito, 'last_message_time': datetime.now(), 'timeout_count': 0, 'state_history': cache_data['state_history']}

        guardar_conversacion(from_number, response_message, 'chatbot')

        client.messages.create(
            body=response_message,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=from_number
        )
    print(f"Historico de estados: {cache_data['state_history']}")
    return '', 200

# Run the Flask app
if __name__ == '__main__':
    for index, row in base_cobranza.iterrows():
        credito = row['Credito']
        numero = f"whatsapp:+57{row['Numero']}"
        nombre = row['Nombre']
        primer_mensaje(credito, numero, TWILIO_WHATSAPP_NUMBER, nombre)

    app.run(host='0.0.0.0', debug=False, port=5000)
