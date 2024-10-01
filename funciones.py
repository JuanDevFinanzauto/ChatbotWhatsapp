from dotenv import load_dotenv
import yaml
import pymssql
import pandas as pd
from datetime import datetime, timedelta
import os
from functools import lru_cache
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
import json
# Cargar variables de entorno
load_dotenv()

# Diccionario para almacenar en caché los datos
data_cache = {}

@lru_cache(maxsize=1000)
def load_data(Credito):
    if Credito in data_cache:
        return data_cache[Credito]

    with open("config.yaml", 'r') as file:
        config = yaml.safe_load(file)
    database_config = config.get('database', {})
    user = database_config.get('username')
    password = database_config.get('password')

    cnxn = pymssql.connect(server='192.168.50.38\DW_FZ', database='DW_FZ', user=user, password=password)

    query1 = f"SELECT * FROM [DW_FZ].[dbo].[CRM_Datos_Consulta_Base] Where Credito = {Credito};"
    query2 = f"SELECT * FROM [DW_FZ].[dbo].[CRM_Datos_Credito] Where Credito = {Credito};"
    query3 = f"SELECT * FROM [DW_FZ].[dbo].[CRM_Datos_Financieros] Where Credito = {Credito} ORDER BY Fecha_pago DESC;"
    query4 = f"SELECT * FROM [Cartera].[dbo].[Asignacion0724] Where credito = {Credito}"

    print(datetime.now())
    BASE = pd.read_sql_query(query1, cnxn)
    print(datetime.now())

    CREDITOS = pd.read_sql_query(query2, cnxn)
    print(datetime.now())
    PAGOS = pd.read_sql_query(query3, cnxn)
    print(datetime.now())

    COBRANZA = pd.read_sql_query(query4, cnxn)
    print(datetime.now())
    # Cedula = BASE["Cedula"][0]
    # query4 = f"SELECT * FROM [DW_FZ].[dbo].[CRM_Datos_Cliente] Where Cedula = {Cedula};"
    # INFO_CL = pd.read_sql_query(query4, cnxn)

    data_cache[Credito] = (BASE, CREDITOS, PAGOS,COBRANZA)
    return BASE.fillna(0), CREDITOS.fillna(0), PAGOS.fillna(0), COBRANZA.fillna(0)

def enviar_correo(destinatario: str, asunto: str, cuerpo: str):


    destinatario = "manuel.arias@finanzauto.com.co"

    # email_cliente = INFO_CL["Correo"]

    with open("config.yaml", 'r') as file:
        config = yaml.safe_load(file)
    
    smtp_config = config.get('smtp', {})
    smtp_user = smtp_config.get('user')
    smtp_password = smtp_config.get('password')

    sender_email = smtp_user
    smtp_server = 'smtp.gmail.com'
    smtp_port = 587

    message = MIMEMultipart()
    message['From'] = sender_email
    message['To'] = destinatario
    message['Subject'] = asunto
    message.attach(MIMEText(cuerpo, 'plain'))

    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(sender_email, destinatario, message.as_string())

    print('Correo enviado con éxito.')

def guardar_en_csv(tipo_carga, credito_num, cliente_info, fecha, razon, mensaje_cliente):
    if tipo_carga == "fecha_monto":
        csv_filename = 'acuerdos_pago.csv'
    elif tipo_carga == "razon":
        csv_filename = 'razones_no_pago.csv'
    elif tipo_carga == "alternativa":
        csv_filename = 'alternativas.csv'
    else:
        raise ValueError(f"Tipo de carga no reconocido: {tipo_carga}")

    feedback_df = pd.DataFrame({
        'Tipo de carga': [tipo_carga],
        'Credito': [credito_num],
        'Cliente': [cliente_info],
        'Fecha': [fecha],
        'Razón': [razon],
        'Mensaje Cliente': [mensaje_cliente]
    })

    if os.path.exists(csv_filename):
        feedback_df.to_csv(csv_filename, mode='a', header=False, index=False, sep=";")
    else:
        feedback_df.to_csv(csv_filename, mode='w', header=True, index=False, sep=";")

def extract_payment_info(user_query: str) -> dict:
    fecha_hoy = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    template = f"""
    fecha de hoy: {fecha_hoy}
    Analiza el siguiente mensaje del cliente y extrae la fecha de pago y el monto a pagar.

    Mensaje del cliente: {user_query}

    Responde solo con un JSON en este formato:
    
        "fecha_pago": "DD-MM-YYYY",
        "monto_pagar": "XXXXXXXX"
    
    monto_pagar el formato de ejemplo es: "monto_pagar": "99999"
    RESPONDE UNICAMENTE EN FORMATO JSON CON LAS LLAVES ESPECIFICADAS.
    SOLO JSON.
    RESPONDE SOLO EL JSON CON LA INFORMACION QUE EL CLIENTE HA INGRESADO. 
    SOLO JSON CON LA INFO QUE EL CLIENTE HA INGRESADO
    """

    prompt = ChatPromptTemplate.from_template(template)
    llm = ChatGroq(groq_api_key=os.environ['GROQ_API_KEY'], model_name="llama3-70b-8192")
    chain = prompt | llm | JsonOutputParser()

    response = chain.invoke({"user_query": user_query, "fecha_hoy": fecha_hoy})
    
    try:
        return response
    except Exception as e:
        print(f"Error parsing JSON response: {e}")
        return {}
    
def decision_llm(user_query: str) -> dict:
    
    template = f"""
Clasifica el siguiente mensaje del cliente en una de las siguientes categorías: afirmativo, negativo, pregunta o inconformidad sobre su crédito, solicitud de hablar con un humano, o confirmación de pago.

Mensaje del cliente: {user_query}.

Responde únicamente con un objeto JSON que contenga una sola clave "decision" y uno de los siguientes valores:

- "yes": Si el mensaje es afirmativo.
- "no": Si el mensaje es negativo.
- "question": Si el cliente hace una pregunta, expresa una inconformidad, o su mensaje no es claramente afirmativo ni negativo.
- "human": Si el cliente pide hablar con una persona o humano.
- "payed": Si el cliente indica que ya ha realizado un pago.
- "atras": Si el cliente desea volver al estado anterior del flujo.

Si el mensaje es "1", clasifícalo como "yes".
Si el mensaje es "2", clasifícalo como "no".
Si el mensaje es "3", clasifícalo como "atras".

Ejemplo de formato de respuesta:
'''

"decision": "yes"

'''
IMPORTANTE:
- Responde SOLO con el objeto JSON.
- No incluyas ninguna explicación adicional.
- Basa tu decisión únicamente en el contenido del mensaje del cliente.
- Asegúrate de que tu respuesta sea válida en formato JSON.
- Asegúrate de que tu respuesta sea válida en formato JSON.
- Asegúrate de que tu respuesta sea válida en formato JSON.
- Asegúrate de que tu respuesta sea válida en formato JSON.
"""


    prompt = ChatPromptTemplate.from_template(template)
    llm = ChatGroq(groq_api_key=os.environ['GROQ_API_KEY_2'], model_name="llama3-70b-8192")
    chain = prompt | llm | JsonOutputParser()

    response = chain.invoke({"user_query": user_query})
    
    try:
        decision = response
        return decision
    except Exception as e:
        print(f"Error parsing JSON response: {e}")
        return {}



def segmentar_opciones_no_pago(user_input):
    template = f"""
    Analiza el siguiente mensaje del cliente y segmenta las posibles opciones por no pagar:

    Mensaje del cliente: {user_input}

    Responde solo con un JSON:
    opciones_no_pago:

    - insolvencia
    - embargo
    - judicial
    - siniestro
    - prevalencia
    - captura_vehiculo
    - no_tiene_recursos
    - otro
    
    "opciones_no_pago":"opcion"
    llave "opciones_no_pago"
    Responde solo con un JSON. FORMATO JSON.
    """

    prompt = ChatPromptTemplate.from_template(template)
    llm = ChatGroq(groq_api_key=os.environ['GROQ_API_KEY'], model_name="llama3-70b-8192")
    chain = prompt | llm | JsonOutputParser()

    response = chain.invoke({"user_query": user_input})
    return response["opciones_no_pago"] if "opciones_no_pago" in response else []


def segmentar_opciones_alternativas(user_input):
    template = f"""
    Analiza el siguiente mensaje del llm y segmenta las posibles alternativas dadas por el llm:

    Mensaje del llm: {user_input}

    Responde solo con un JSON:

    opciones_alternativa:

    - Negociación
    - Medios de pago
    - Programar recaudo
    - Reestructuración
    - Daciones
    - cambio fecha
    - meses de gracia
    - refinanciacion
    - traslado de cuota
    - extension de plazo

    numero_asignado:

    - wa.me/573195032079 
    - wa.me/573226782950
    - wa.me/573506864534
    - wa.me/573158304884

    
    Ejemplos:
    '''
        "opciones_alternativa": "Negociación"
        "numero_asignado": "wa.me/573195032079"

    '''
    '''
        "opciones_alternativa": "Reestructuración"
        "numero_asignado": "wa.me/573506864534"

    '''

    Respond con el JSON dada la respuesta del llm: {user_input}
    Responde solo con un JSON. FORMATO JSON.
    """

    prompt = ChatPromptTemplate.from_template(template)
    llm = ChatGroq(groq_api_key=os.environ['GROQ_API_KEY'], model_name="llama3-70b-8192")
    chain = prompt | llm | JsonOutputParser()

    response = chain.invoke({"user_query": user_input})
    return response["opciones_no_pago"] if "opciones_no_pago" in response else []

def question_answer(input_user, chat_history, BASE, CREDITOS, PAGOS,resultado,valor_cuota):
    try:
        BASE = BASE.drop(columns=["Cedula"])
        PAGOS_question = PAGOS.drop(columns=["Identificacion"])
    except:
        pass
    print(valor_cuota)
    cliente_info = BASE.to_dict('records')[0] if not BASE.empty else {}
    credito_info = CREDITOS.to_dict('records')[0] if not CREDITOS.empty else {}
    pagos_info = PAGOS_question.to_dict('records')[:50] if not PAGOS_question.empty else []
    
    llm = ChatGroq(groq_api_key=os.environ['GROQ_API_KEY'], model_name="llama3-70b-8192")
    
    template = """
Eres el mejor asistente de cobranza de Finanzauto. 
Responde a las preguntas del cliente solo si se trata de Finanzauto o su credito y sigue las siguientes instrucciones e información, solo responde con respecto al contexto que se te otorga de lo contrario puedes responder que no sabes.

Usa esta información:

Conversacion whatsapp historica:{chat_history}
Cliente: {cliente_info}
Crédito: {credito_info} "Valor_cuota: "{valor_cuota}"
Dias_mora: numero (entero).

Pagos(Máximo últimos 50 pagos): {pagos_info}
Pregunta: {user_query}



Siempre que el cliente diga "reporte" da el link proporcionado. (Si no hay no des reporte)
LINK de REPORTE: {link_reporte} (Contraseña: cédula del cliente)

Oficinas y Horarios:

- Sede Américas / Bogotá: Kr 56 N° 09 – 17 Torre Central. Lun-Vie 7am-7pm, Sáb 8am-3pm, Dom 9am-3pm.
- Sede Norte / Bogotá: Calle 116 N° 23–06 Edificio Business Center 04. Lun-Vie 7am-7pm, Sáb 8am-2pm.
- Sede Abastos / Bogotá: Av. Cra.80 N°2-51 Bodega 41 Local 9. Lun-Vie 7am-4pm, Sáb 7am-12pm.
- Villavicencio: Cra 33 N° 15 – 28 Edif. Casa Toro Km 1 Vía Pto López Of. 101. Tel: (608) 667 5986. Lun-Vie 8am-5:30pm, Sáb 8am-1pm.
- Bucaramanga: Calle 53 N° 23-97. Tel: 3132242424. Lun-Vie 8am-5:30pm, Sáb 8am-1pm.
- Barranquilla: Cra 52 N° 74-39. Tel: 3223074332. Lun-Vie 8am-5:30pm, Sáb 8am-1pm.
- Medellín: Cra 43a N° 23-25 local 128 c.c. avenida mall. Lun-Vie 8am-5:30pm, Sáb 8am-1pm.
- Cali: Calle 40 norte N° 6N-28 La campiña. Tel: (602) 489 2679. Lun-Vie 8am-5:30pm, Sáb 8am-1pm. Cel: 3202146710.
- Cartagena: Cra 15 #31-110 Local 12 Centro -Cultural y Turístico San Lázaro Barrio El Espinal. Tel: 3160103514. Lun-Vie 8am-5:30pm, Sáb 8am-1pm.
- Pereira: Av. 30 de agosto #94 Esquina los Coches. Tel: 3105170749.

Cuentas para Pagos:

- Corriente: 
  - Banco Bogotá: Convenio recaudo: 7285
  - Bancolombia: Convenio recaudo: 63437 - 87912
  - Banco de Occidente: Convenio recaudo: 012948
  - Colpatria: Convenio recaudo: 160
  - Banco Davivienda: Convenio recaudo: 1372994 - 1392653
  - Banco Av Villas: Convenio recaudo: 018130666
  - Banco GNB Sudameris:Convenio recaudo: 480

Envía soporte de pago a: pagos@finanzauto.com.co con tus datos y número del crédito. La actualización se reflejará en cuatro días hábiles.

Débito Automático: Solicítalo en la web o por correo.

Recaudo a Domicilio: Llama al 3107923738 o 3107923729. Lun-Vie 8am-4pm, Sáb 8am-12pm.

Pago Ágil: https://www.finanzauto.com.co/portal/pago-agil/

Reestructuración o Problemas de Pago: Llama a cobranza al (601) 749000 opción 2.

Notas IMPORTANTES:
- SI EL CLIENTE SOLICITA LA LIQUIDACIÓN DE SU CREDITO, REFIERE LA SOLICITUD DE LA SIGUIENTE MANERA:"Es necesario solicitar la liquidación total del crédito, la cual puede ser obtenida por medio 
telefónico o por medio del correo servicioalcliente@finanzauto.com.co. El tiempo de entrega está 
estimado en dos (2) días hábiles y será enviado al correo electrónico registrado.  
En caso de ser requerido el documento antes del tiempo informado, podrá dirigirse directamente a 
nuestras oficinas, con un tiempo de entrega aproximado de una hora."
- No saludes.
- Sé cortés y amigable. puedes usar emojis.
- Si el crédito está "Cancelado", notifícalo al cliente.
- Si no tienes suficiente información, responde "No sé".
- No inventes información. Solo proporciona la información dada.
- Formato de pesos colombianos: $X.XXX.XXX.
- Termina siempre, siempre always termina lo que digas con: "¿Quieres realizar el pago de tu cuota *hoy*?"
- Recuerda: No CALCULES LIQUIDACIONES o cuando digan "Liquidacion" SOLAMENTE REFIERE A servicioalcliente@finanzauto.com.co PARA LIQUIDACIONES.
- Recuerda: Para liquidar solamente, unicamente en  servicioalcliente@finanzauto.com.co.
- Para liquidar solamente, unicamente en  servicioalcliente@finanzauto.com.co.
- Recuerda: No CALCULES LIQUIDACIONES o cuando digan "Liquidacion" SOLAMENTE REFIERE A servicioalcliente@finanzauto.com.co PARA LIQUIDACIONES.
- Recuerda: Para liquidar solamente, unicamente en  servicioalcliente@finanzauto.com.co.

    """
    
    prompt = ChatPromptTemplate.from_template(template)
    chain = prompt | llm | StrOutputParser()
    
    response = chain.invoke({
        "chat_history": chat_history[:1000],
        "user_query": input_user,
        "pagos_info": pagos_info,
        "credito_info": credito_info,
        "cliente_info": cliente_info,
        "link_reporte":resultado,
        "valor_cuota" : valor_cuota
    })
    return response

def human_transfer(input_user, chat_history, BASE, CREDITOS, PAGOS,resultado):
    try:
        BASE = BASE.drop(columns=["Cedula"])
        PAGOS_question = PAGOS.drop(columns=["Identificacion"])
    except:
        pass
    cliente_info = BASE.to_dict('records')[0] if not BASE.empty else {}
    credito_info = CREDITOS.to_dict('records')[0] if not CREDITOS.empty else {}
    pagos_info = PAGOS_question.to_dict('records')[:50] if not PAGOS_question.empty else []
    
    llm = ChatGroq(groq_api_key=os.environ['GROQ_API_KEY'], model_name="llama3-70b-8192")
    
    template = """
Eres el mejor asistente de cobranza de Finanzauto. 
Responde a las preguntas del cliente solo si se trata de Finanzauto o su credito y sigue las siguientes instrucciones e información, solo responde con respecto al contexto que se te otorga de lo contrario puedes responder que no sabes.

Usa esta información:

Conversacion whatsapp historica:{chat_history}
Cliente: {cliente_info}
Crédito: {credito_info}
Pagos(Máximo últimos 50 pagos): {pagos_info}
Pregunta: {user_query}

Siempre que el cliente diga "reporte" da el link proporcionado. (Si no hay no des reporte)
LINK de REPORTE: {link_reporte} (Contraseña: cédula del cliente)

SI EL CLIENTE HA DICHO QUE QUIERE HABLAR CON UNA PERSONA UN HUMANO O ALGO POR EL ESTILO RECOMIENDALE ESCRIBIR AL WHATSAPP(dilo amablemente):

wa.me/573195032079 o wa.me/573226782950 o wa.me/573506864534 o wa.me/573158304884 escoge para mostrar aleatoriamente solo uno nunca cambies los numeros y la forma es exacta.

SI EL CLIENTE HA DICHO QUE YA HA PAGADO ENTONCES RESPONDE AGRADECIDO PERO PIDELE AMABLEMENTE QUE ENVIE EL CERTIFICADO AL CORREO: pagos@finanzauto.com.co anexando sus datos 
personales y número del crédito ({credito_info}s).
SE amable PUEDES USAR EMOJIS😁. PUEDES TUTEAR. 
NO SALUDES.
No saludes nunca.
Nunca saludes.
NUNCA DIGAS "HOLA", "Hola"
No saludes.
NADA DE "HOLA".

    """
    
    prompt = ChatPromptTemplate.from_template(template)
    chain = prompt | llm | StrOutputParser()
    
    response = chain.invoke({
        "chat_history": chat_history[:1000],
        "user_query": input_user,
        "pagos_info": pagos_info,
        "credito_info": credito_info,
        "cliente_info": cliente_info,
        "link_reporte":resultado
    })
    return response

def pagado_response(input_user, chat_history, BASE, CREDITOS, PAGOS,resultado):
    try:
        BASE = BASE.drop(columns=["Cedula"])
        PAGOS_question = PAGOS.drop(columns=["Identificacion"])
    except:
        pass
    cliente_info = BASE.to_dict('records')[0] if not BASE.empty else {}
    credito_info = CREDITOS.to_dict('records')[0] if not CREDITOS.empty else {}
    pagos_info = PAGOS_question.to_dict('records')[:50] if not PAGOS_question.empty else []
    
    llm = ChatGroq(groq_api_key=os.environ['GROQ_API_KEY'], model_name="llama3-70b-8192")
    
    template = """
Eres el mejor asistente de cobranza de Finanzauto. 
Responde a las preguntas del cliente solo si se trata de Finanzauto o su credito y sigue las siguientes instrucciones e información, solo responde con respecto al contexto que se te otorga de lo contrario puedes responder que no sabes.

Usa esta información:

Conversacion whatsapp historica:{chat_history}
Cliente: {cliente_info}
Crédito: {credito_info}
Pagos(Máximo últimos 50 pagos): {pagos_info}
Pregunta: {user_query}

Siempre que el cliente diga "reporte" da el link proporcionado. (Si no hay no des reporte)
LINK de REPORTE: {link_reporte} (Contraseña: cédula del cliente)


SI EL CLIENTE HA DICHO QUE YA HA PAGADO ENTONCES RESPONDE AGRADECIDO PERO PIDELE AMABLEMENTE QUE ENVIE EL CERTIFICADO AL CORREO: pagos@finanzauto.com.co anexando sus datos 
personales como cedula, numero de celular y número del crédito ({credito_info}).

    """
    
    prompt = ChatPromptTemplate.from_template(template)
    chain = prompt | llm | StrOutputParser()
    
    response = chain.invoke({
        "chat_history": chat_history[:1000],
        "user_query": input_user,
        "pagos_info": pagos_info,
        "credito_info": credito_info,
        "cliente_info": cliente_info,
        "link_reporte":resultado
    })
    return response


def conversation_node(input_user, chat_history, BASE, CREDITOS, PAGOS, COBRANZA):


    cliente_info = BASE.to_dict('records')[0] if not BASE.empty else {}
    credito_info = CREDITOS.to_dict('records')[0] if not CREDITOS.empty else {}
    pagos_info = PAGOS.to_dict('records')[:10] if not PAGOS.empty else []
    cobranza_info = COBRANZA[COBRANZA[['daciones', 'cambio_fecha', 'refinanciacion']].isin(['Si']).any(axis=1)].to_dict('records')[:10] if not COBRANZA.empty else []

    llm = GoogleGenerativeAI(model="models/gemini-1.5-pro-exp-0827", google_api_key=os.environ['GOOGLE_API_KEY_AI'])
    
    template = """
    Eres un asistente de cobranza de Finanzauto. Usa la siguiente información para responder:
    
    Información del cliente: {cliente_info}
    Información del crédito (Mora_actual en días): {credito_info} 
    
    Historial de pagos: {pagos_info}

    Historial del chat: {chat_history}
    
    Razon de no pago: {user_query}

    Pueden escribir al whatsapp de negociaciones con un agente personal por whatsapp a : wa.me/573195032079 o wa.me/573226782950 o wa.me/573506864534 o wa.me/573158304884 escoge para mostrar aleatoriamente solo uno nunca cambies los numeros y la forma es exacta.

    De lo que debe siempre da el valor de la cuota como el valor adeudado.

    las alternativas que tiene el cliente son:{cobranza_info}
    

Motivos de no pago -> Alternativa
Espera Ingreso Futuro -> Negociación
Desempleo -> Negociación
Retraso en Pagos -> Negociación
Cliente de viaje o reside en otra ciudad o país -> Negociación
Olvido Pago -> Medios de pago
Dificultad de Salud -> Negociación
Sin Entidades Bancarias Cerca -> Programar recaudo
Disminución de Ingresos Familiares -> Reestructuración
Suspensión de Contrato -> Negociación
Movimiento No Coincide Con Fecha de Pago -> Negociación
Disminución de Ingresos - Bonificaciones - Comisiones -> Reestructuración

Negociación es: Reestructuración - Refinanciación, Cancelaciones, Daciones
-----------------------------------------------------------
Reestructuración:
Son aquellos créditos con mora inferior o igual a 90 días que tienen modificación en
las condiciones inicialmente pactadas ante un potencial o real deterioro de la
capacidad de pago.
Diligenciar el formato para solicitud del mecanismo.
Análisis de la viabilidad del mecanismo.
La elaboración de las garantías está a cargo de Operaciones, quienes
toman las firmas y tramitan el respectivo desembolso para posterior registro
en signature.
Firma de pagaré que deberá contener titular y avalista inicial si así lo requiere.

Condiciones:
El crédito se encuentre al día o en mora máximo de 90 días.
Tener mínimo 12 cuotas canceladas.
El crédito no se encuentre en estado C (INSOLVENCIA) y aparte La liquidación del
crédito no puede superar el capital inicialmente desembolsado.
LTV mayor o igual a 80%
------------------------------------------------------------

Refinanciación
Son aquellos créditos con mora superior a 90 días que tienen modificación en las
condiciones inicialmente pactadas ante un potencial o real deterioro de la capacidad de
pago.
Condiciones:
El crédito presenta mora proyectada mayor o igual a 91 días.
Tiene Mínimo 12 cuotas canceladas.
No aplica para clientes en estado C (INSOLVENCIA).
La liquidación del crédito no puede superar el capital inicialmente desembolsado.
Valor de la Garantía debe ser superior al valor de la liquidación del crédito.
Se valida centrales de riesgo y capacidad de pago y el LTV (no superar el LTV inicial).
LTV mayor o igual a 80%
Se debe generar abono de mínimo 30% de la mora según negociación de la jefatura.
-------------------------------------------------------------
Daciones
Tabla de Condiciones según la Franja de Mora Proyectada:
Franja de mora proyectada	Objetivo del pago	Capital	Interés Cte	Interés Mora	Seguro de vida	Otros cargos
1 - 30 días	Cancelación total	0%	0%	0%	0%	0%
31 - 60 días	Cancelación total	0%	50%	100%	0%	0%
61 - 90 días	Cancelación total	0%	50%	100%	0%	0%
91 - 150 días	Cancelación total	0%	60%	100%	0%	0%
151 - 180 días	Cancelación total	0%	70%	100%	0%	0%
181 - 360 días	Cancelación total	0%	80%	100%	0%	0%
>360 días	Cancelación total	0%	100%	100%	0%	0%
Notas importantes:

Si el porcentaje de la atribución supera lo establecido en la tabla, únicamente puede ser aprobado por la respectiva dirección de cartera.
Dentro de las condonaciones, se exceptúan las insolvencias por el tipo de proceso que se adelanta en estos casos.

------------------------------------------------------------
Cambios de fecha:
Cuando un deudor de cartera vigente o vencida presenta dificultad para pagar en fecha
corte pactada en el pagaré, se puede ofrecer cambio de fecha, esta alternativa se puede
utilizar una sola vez en la vida del crédito, en relación con el título valor pagaré suscrito por
el cliente, se modifica el día de vencimiento de cada uno de los instalamentos.
Dicha modificación, causa interés corriente por los días corridos, suma que puede ser
cargada al crédito en la próxima cuota facturada.
Lo anterior, modifica la tabla de amortización diseñada inicialmente para el crédito,
generando una cuota de mayor valor al finalizar el plazo del crédito. De igual forma, al
modificar la fecha pago, el cliente debe asumir el valor de primas de seguro causadas.

Condiciones:
- El crédito se encuentre al día o hasta 60 días en mora.
- No aplica para créditos en estado C (INSOLVENCIA)
- Aplica para los créditos en estado J (JUDICIAL) y únicamente para los casos que se
adelantan por el proceso de Garantía Mobiliaria.
- No aplica para los créditos en estado J (JUDICIAL) que se adelantan por proceso
ejecutivo.
- El cliente debe pagar los intereses corrientes de los días a mover o excepcionalmente
se podrán pasar a la siguiente cuota.
- La nueva fecha máximo puede ser hasta el día 5 o con autorización al 10.
- El cambio de fecha se puede aplicar una vez en la vida del crédito cuando el crédito
está en mora.
- Aplica para alivios financieros PAD 50, 80 y 81.
- Fechas de facturación son del 25 al 31 de cada mes, excepcionalmente facturación del
15 al 24 con más de 6 cuotas pagas. Si no está entre el 25-31 solicitar aprobación.
------------------------------------------------------------
Meses de gracia
Son dos a tres meses máximo que se le otorga al cliente, donde no realiza pago de
cuota completa, solo se contempla seguros incluido el GPS.
Cuando un deudor de cartera vigente o vencida presenta una novedad en la entrega del
vehículo atribuible al concesionario, se puede ofrecer esta alternativa.

Condiciones:
- El crédito se encuentre al día o en mora máximo de 90 días.
- Aplican 2 meses para vehículos particulares y 3 meses para públicos.
- El crédito no debe tener como más de 4 pagos.
- Solo se puede realizar una vez en la vida del crédito
------------------------------------------------------------
Traslado de cuota
Cuando un crédito presenta hasta 2
cuotas vencidas, se pacta con el
deudor la normalización de su
obligación, para que estas cuotas sean
cargadas al finalizar la amortización
del crédito.
Condiciones:
- El deudor por causas ajenas a su voluntad; incapacidades, desvinculación
laboral temporal, siniestros del vehículo, presenta hasta 2 cuotas vencidas y
con excepción de gerencia hasta 3 cuotas.
- Franja de mora proyectada 31-60.
- Abono en el mes en curso del 30% sobre el valor de la cuota
- Clientes que realicen la respectiva solicitud. 
------------------------------------------------------------
Extensión de plazo
Esta alternativa consiste en extender el plazo del crédito inicialmente pactado de
acuerdo a lo solicitado por el cliente. Cuando el crédito tiene vencidos intereses
corrientes, intereses moratorios, seguros y gastos adicionales, se genera un cargo el
cual puede ser asumido por el cliente o diferido en el nuevo plazo pactado
Condiciones:
- Franja de mora proyectada 31 - 90 días.
- No aplica para créditos judiciales, insolventes o adjudicación. 
- El crédito debe tener un riesgo bajo.
- Altura cuotas pagas mayor o igual 12.
- Debe tener un LTV inferior al 80%.
- Debe tener un saldo a capital mayor a 2.000.000
------------------------------------------------------------


    Si existe SOLO una alternativa para el cliente escoge la mejor para el cliente dada la razon de no pago y  tambien dale el telefono de un agente de whatsapp para que el cliente concrete esa opcion,
    Siempre da un agente de whatsapp con la alternativa ofrecida 

    Redacta la respuesta como respuesta amigable al cliente de la solucion insinuando a que concrete esta alternativa con un agente personal.
    
    - Nunca dar valores exactos de eso se encarga el otro numero de whatsapp (limita tu respuesta a 1000 caracteres maximo)
    - NUNCA CALCULES LIQUIDACIONES SOLAMENTE REFIERE A servicioalcliente@finanzauto.com.co PARA LIQUIDACIONES.
    - SI EL CLIENTE SOLICITA LA LIQUIDACIÓN DE SU CREDITO, REFIERE LA SOLICITUD DE LA SIGUIENTE MANERA:"Es necesario solicitar la liquidación total del crédito, la cual puede ser obtenida por medio 
telefónico o por medio del correo servicioalcliente@finanzauto.com.co. El tiempo de entrega está 
estimado en dos (2) días hábiles y será enviado al correo electrónico registrado.  
En caso de ser requerido el documento antes del tiempo informado, podrá dirigirse directamente a 
nuestras oficinas, con un tiempo de entrega aproximado de una hora."
Recuerda: No CALCULES LIQUIDACIONES o cuando digan "Liquidacion" SOLAMENTE REFIERE A servicioalcliente@finanzauto.com.co PARA LIQUIDACIONES.
Recuerda: Para liquidar solamente, unicamente en  servicioalcliente@finanzauto.com.co.
    - Responde de manera concisa y profesional, enfocándote en temas de cobranza y estado del crédito.
    - NO HAGAS PREGUNTAS EXTRA HAZLO COMO FINALIZANDO UNA CONVERSACIÓN.
    - SE amable PUEDES USAR EMOJIS. PUEDES TUTEAR. 
    - NO SALUDES.
    - No saludes nunca.
    - Nunca saludes.
    - NUNCA DIGAS "HOLA", "Hola"
    - No saludes.
    - NADA DE "HOLA".
    - Formato : $ XXX.XXX
    - Para liquidar solamente, unicamente en  servicioalcliente@finanzauto.com.co.
    """
    
    prompt = ChatPromptTemplate.from_template(template)
    chain = prompt | llm | StrOutputParser()
    
    response = chain.invoke({
        "chat_history":chat_history,
        "user_query": input_user,
        "pagos_info": pagos_info,
        "credito_info": credito_info,
        "cobranza_info": cobranza_info,
        "cliente_info": cliente_info
    })
    return response

def consultar_extracto(identificacion, anno, credito, mes, id_aplicativo, firma):
    # URL de la API
    url = "https://www.finanzauto.com.co/Services/ApiWeb/api/ConsultarExtracto"
    
    # Datos para la solicitud
    payload = {
        "Identificacion": identificacion,
        "Anno": anno,
        "Credito": credito,
        "Mes": mes, 
        "IdAplicativo": id_aplicativo,
        "Firma": firma
    }
    
    # Encabezados de la solicitud (si es necesario)
    headers = {
        "Content-Type": "application/json"
    }
    
    # Realizar la solicitud POST
    response = requests.post(url, headers=headers, data=json.dumps(payload))
    
    # Verificar si la solicitud fue exitosa
    if response.status_code == 200:
        # Devolver la respuesta JSON
        return response.json()
    else:
        # Devolver un mensaje de error
        return {"Error": response.status_code, "Message": response.text}
    
def get_datos_pago(identificacion, loan_number, firma="E0O3puuYt4RjdMjXSd+Biw==", key=None,OriginTypeId= "8"):
    url = "http://www.finanzauto.info/Services/WebApiPagos/api/GetDatosPago"
    headers = {
        "Content-Type": "application/json"
    }
    data = {
        "Identification": identificacion,
        "LoanNumber": loan_number,
        "Firma": firma,
        "OriginTypeId": OriginTypeId
    }
    if key:
        data["Key"] = key

    response = requests.post(url, json=data, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        response.raise_for_status()


def process_chat(credito, user_input, step, chat_history=[]):

    BASE, CREDITOS, PAGOS, COBRANZA = load_data(credito)

    if COBRANZA["EstadoCartera"][0] == "JUDICIAL":
        valor_cuota = CREDITOS["Valor_cuota"][0] + CREDITOS["Valor_cuota"][0] * 0.2
        CREDITOS = CREDITOS.drop(columns=["Valor_cuota"])
        print("Valor cuota borrado")
    else:
        valor_cuota = CREDITOS["Valor_cuota"][0] 


    try:
        # Ejemplo de uso de la función
        identificacion = str(int(PAGOS["Identificacion"][0]))
        anno = datetime.now().year
    except:
        print(f"+-+-+-+-+-ERROR EN OBTENER ID|||{Exception}||||+-+-+-+-+-")
        
    mes = datetime.now().month
    id_aplicativo = 1
    firma = "nDOm4Ypfo26J4jWBEtcKNg=="
    OriginTypeId =  "8"

    
    try:
            decision = decision_llm(user_input)
            # print(decision)
            yes_no = decision.get("decision", "question")
    except Exception as e:
        print(f"--------------{e}-----------")
        return "Por favor me puedes volver a responder, no te comprendí muy bien", step
    
    user_input = user_input.lower()
    monto_num = valor_cuota
    monto = "${:,.0f}".format(monto_num)
    num_credito = CREDITOS["Credito"][0]
    nombre = BASE['Nombre'][0].lower().title()
    dias_mora = int(CREDITOS["Mora_actual"][0])

    fecha_proximo_pago = CREDITOS["Fecha_proximo_pago"][0]
    print(f"Step: {step} | Decision: {yes_no}")
    
    url_pago_agil = "https://www.finanzauto.com.co/portal/pago-agil"
    resultado = {}
    if step != "fecha_monto" and step !="razon" and step !="fin":
        if yes_no == "question":
                    resultado["RutaDocumento"] = ""
                    try:
                        resultado = consultar_extracto(identificacion, anno, credito, mes, id_aplicativo, firma)
                    except:
                        pass
                    # print(f"----{identificacion}----{anno}-----{mes}---------{resultado}--------------------")
                    # print(f"{user_input}-----------{chat_history}--------------{BASE}-----------{CREDITOS}-----------{PAGOS}-----")
                    return question_answer(user_input, chat_history, BASE, CREDITOS, PAGOS,resultado["RutaDocumento"],monto).replace("Hola!","").replace("Hola!,","").replace("Hola,","").replace("Hola","").replace("¡!","").replace("¡",""), "debe_dinero"
        elif yes_no == "payed":
                    resultado["RutaDocumento"] = ""
                    try:
                        resultado = consultar_extracto(identificacion, anno, credito, mes, id_aplicativo, firma)
                    except:
                        pass
                    # print(f"{user_input}-----------{chat_history}--------------{BASE}-----------{CREDITOS}-----------{PAGOS}-----")
                    return pagado_response(user_input, chat_history, BASE, CREDITOS, PAGOS,resultado["RutaDocumento"]).replace("Hola!,","").replace("Hola,","").replace("Hola","").replace("¡!","").replace("¡",""), "fin"
        elif yes_no == "human":
                    resultado["RutaDocumento"] = ""
                    try:
                        resultado = consultar_extracto(identificacion, anno, credito, mes, id_aplicativo, firma)
                    except:
                        pass
                    # print(f"----{identificacion}----{anno}-----{mes}---------{resultado}--------------------")
                    # print(f"{user_input}-----------{chat_history}--------------{BASE}-----------{CREDITOS}-----------{PAGOS}-----")
                    return human_transfer(user_input, chat_history, BASE, CREDITOS, PAGOS,resultado["RutaDocumento"]).replace("Hola!,","").replace("Hola,","").replace("Hola","").replace("¡!","").replace("¡",""), "fin"
    else: 
        pass

    if step == "es_cliente":
        if  "yes" in yes_no:
            if dias_mora>=999:
                answer = f"""Te informamos que tu crédito No. {num_credito} presenta un estado jurídico y un saldo pendiente de {monto}. ¿Podrías realizar el pago el día de *hoy*?
    1. Sí
    2. No
                """
                return answer, "debe_dinero"
            else:
                pass
            answer = f"""Te informamos que está pendiente el pago de tu crédito No.{num_credito} que al día de hoy reporta {dias_mora} días de mora por valor de {monto}. Dicho esto, ¿puedes realizar el pago el día de *hoy*?
1. Sí
2. No
            """
            return answer, "debe_dinero"
        elif "no" in yes_no:
            answer = f"""Disculpa la molestia, hemos finalizado el chat. Si necesitas más ayuda, no dudes en contactarnos nuevamente.
            """
            return answer, "fin"
        else:
            return "¿Por favor ingresa una respuesta Si o No?", "es_cliente"
    elif step == "debe_dinero":
        if  "yes" in yes_no:
            try:
                resultado_pago = get_datos_pago(identificacion, credito)

                url_pago_agil = resultado_pago["UrlPagoAgil"]

                monto = "${:,.0f}".format(resultado_pago["Value"])
            except:
                pass
            return f"Entiendo, recuerda que el valor a pagar es de {monto}, puedes realizar el pago en el siguiente enlace {url_pago_agil}. Tu número de crédito es {num_credito}.", "fin"
        elif "no" in yes_no:
            
            answer = f"""Comprendo, ¿Quisieras hacer un acuerdo de pago?"
1. Sí
2. No
            """
            return answer, "acuerdo_pago"
            
    
            
    elif step == "acuerdo_pago":
        if  "yes" in yes_no:
            monto_minimo = int(monto_num) * 0.4
            return f"¡Perfecto! 😊 Puedes realizar un acuerdo de pago con un monto mínimo de {monto_minimo:,.0f} y una fecha límite de {(datetime.now() + timedelta(days=3)).strftime('%d-%m-%Y')}. Por favor, indícame la *fecha* y el *monto* que deseas pagar.","fecha_monto"
        elif "no" in yes_no:
            return "Entiendo, podrías explicarme cuál es el motivo?🤔", "razon"
        else:
            return "¿Por favor ingresa una respuesta Si o No?", "acuerdo_pago"
    elif step == "fecha_monto":
        payment_info = extract_payment_info(user_input)
        if payment_info:
            fecha_pago = payment_info.get("fecha_pago", "No especificada")
            monto_pagar = payment_info.get("monto_pagar", "No especificado")
            try:
                monto_pagar = int(monto_pagar)
                monto_minimo = int(monto_num) * 0.4
                if monto_pagar < monto_minimo:
                    return f"Por favor ingresa nuevamente la fecha y un valor de pago mayor a ${monto_minimo:,.0f}.", "fecha_monto"
                
                fecha_pago_datetime = datetime.strptime(fecha_pago, '%d-%m-%Y')
                if fecha_pago_datetime < datetime.now():
                    return f"Por favor ingresa nuevamente el valor y una fecha de pago después de {datetime.now().strftime('%d-%m-%Y')}", "fecha_monto"
                if fecha_pago_datetime > datetime.now() + timedelta(days=3):
                    return f"Por favor ingresa nuevamente el valor y una fecha de pago antes de {(datetime.now() + timedelta(days=3)).strftime('%d-%m-%Y')}", "fecha_monto"
                
                guardar_en_csv("fecha_monto", num_credito, nombre, datetime.now(), "Acuerdo de pago", f"Fecha de pago: {fecha_pago}, Monto a pagar: {monto_pagar}")
                return f"Acuerdo de pago registrado para el {fecha_pago} por un monto de ${monto_pagar:,.0f}.", "fin"
            except ValueError:
                return "Por favor ingresa valores válidos para la fecha y el monto.", "fecha_monto"
        else:
            return "No se pudo obtener la información de pago. Por favor, intenta nuevamente.", "fecha_monto"
    
    
    elif step == "razon":
        if user_input:
            try:
                opciones_no_pago = segmentar_opciones_no_pago(user_input)
            except:
                pass
            if opciones_no_pago:
                saldo_capital_formateado = "${:,.0f}".format(CREDITOS["saldo_capital_dia"][0])
                
                guardar_en_csv("razon", num_credito, nombre, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), opciones_no_pago, user_input)

                if any(term in opciones_no_pago for term in ["insolvencia", "embargo", "judicial", "siniestro", "prevalencia", "captura_vehiculo"]):
                    enviar_correo(
                        destinatario="manuel.arias@finanzauto.com.co",
                        asunto=f"Notificación de {opciones_no_pago} del Cliente con Número de Crédito {CREDITOS['Credito'][0]}",
                        cuerpo=f"""
Espero que este mensaje le encuentre bien.

Me dirijo a usted para informarle que el cliente con el número de crédito {CREDITOS["Credito"][0]} ha presentado una situación de {opciones_no_pago}. Esto se desprende de nuestra reciente conversación en la que el cliente mencionó: 
'
{user_input}.
'
Detalles relevantes:

Número de Crédito: {CREDITOS["Credito"][0]}
Saldo a Capital: {saldo_capital_formateado}
Días de Mora: {CREDITOS["Mora_actual"][0]}
Agradecería que se tome en cuenta esta situación para los próximos pasos de gestión de cobranza. Por favor, avísenme si requieren información adicional o si necesitan que tomemos alguna acción específica.

Gracias por su atención a este asunto.

Saludos cordiales,
                """
                    )
                
                respuesta_nopago = conversation_node(user_input, chat_history, BASE, CREDITOS, PAGOS,COBRANZA).replace("Hola!,","").replace("Hola,","").replace("Hola","").replace("¡!","").replace("¡",""), "fin"
                alternativa = segmentar_opciones_alternativas(respuesta_nopago)
                guardar_en_csv("alternativa", num_credito, nombre, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), alternativa, respuesta_nopago)

                return respuesta_nopago, "fin"
            else:
                print(f"###ERROR :{Exception}###")
                return "No se pudo determinar la razón. Por favor, sé más específico.", "razon"
            
        else:
            return "Por favor, danos razones válidas", "razon"
    elif step == "fin":
        return "Gracias por utilizar nuestro servicio. El chat ha finalizado, pero estamos aquí para ayudarte cuando lo necesites.", "no_mensaje"
        
    else:
        return "Gracias por utilizar nuestro servicio. El chat ha finalizado, pero estamos aquí para ayudarte cuando lo necesites. 😁", "no_mensaje"


