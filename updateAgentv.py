from pydantic import BaseModel, Field, validator, PrivateAttr
from typing import Optional, List
from threading import Timer, Lock
import time
import os
from datetime import datetime, timedelta
import sqlite3
from dotenv import load_dotenv
from langchain_groq import ChatGroq

# Cargar variables de entorno
load_dotenv()

# Wrapper para el modelo de lenguaje
class ChatGroqWrapper:
    def __init__(self, groq_api_key, model_name):
        self.llm = ChatGroq(api_key=groq_api_key, model_name=model_name)
        self.system_prompt = (
            "Eres un asistente virtual útil de Finanzauto. "
            "Tu tarea es asistir a los usuarios proporcionando información y recopilando comentarios. "
            "Cuando interactúes con los usuarios, solicita educadamente la información que necesites, "
            "y nunca proporciones tu propia información personal ni identifiques que eres un asistente virtual."
        )

    def send_message(self, user_id, instruction):
        prompt = f"""{self.system_prompt}

Usuario ID: {user_id}
{instruction}

Asistente:"""
        result = self.llm.invoke(prompt)
        assistant_reply = result.content.strip()
        print(f"Assistant: {assistant_reply}")  # Para depuración
        return assistant_reply

# Inicializar el asistente de lenguaje
llm = ChatGroqWrapper(
    groq_api_key=os.environ['GROQ_API_KEY'],
    model_name="llama3-70b-8192"
)

# Definir el modelo de datos del usuario
class UserData(BaseModel):
    user_id: str
    start_time: float = Field(default_factory=time.time)
    conversation_active: bool = True
    reminders_sent: int = 0

    # Información del usuario
    credit_number: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    reason: Optional[str] = None
    selected_slot: Optional[str] = None

    # Estado de la conversación
    awaiting_response: bool = False
    appointment_requested: bool = False
    awaiting_slot_selection: bool = False
    awaiting_appointment_details: bool = False
    appointment_details_step: int = 0
    satisfaction_attempts: int = 0
    rated: bool = False
    rating_requested: bool = False
    consultation_received: bool = False

    # Nodo actual
    current_node: Optional[str] = None

    # Bloqueo y temporizador
    _timer_lock: Lock = PrivateAttr(default_factory=Lock)
    _timer: Optional[Timer] = PrivateAttr(default=None)

# Definir los posibles estados de la conversación
class GraphState(BaseModel):
    user_data: UserData
    incoming_msg: Optional[str] = None

    @validator('incoming_msg')
    def validate_message(cls, v):
        return v.strip() if v else v

# Inicializar el estado de usuarios
user_states = {}

# Configuración de la base de datos
def init_db():
    conn = sqlite3.connect('citas.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS citas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            credit_number TEXT,
            first_name TEXT,
            last_name TEXT,
            phone_number TEXT,
            reason TEXT,
            appointment_datetime TEXT
        )
    ''')
    conn.commit()
    conn.close()

# Guardar cita en la base de datos
def save_appointment(user_data):
    conn = sqlite3.connect('citas.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO citas (
            user_id, credit_number, first_name, last_name,
            phone_number, reason, appointment_datetime
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (
        user_data.user_id,
        user_data.credit_number,
        user_data.first_name,
        user_data.last_name,
        user_data.phone_number,
        user_data.reason,
        user_data.selected_slot
    ))
    conn.commit()
    conn.close()

# Definir los nodos del grafo de estados
def validado(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "Por favor, proporcione su número de documento de identidad y su correo electrónico asociado a su cuenta en Finanzauto."
    llm.send_message(user_id, instruction)
    state.user_data.awaiting_response = True
    state.user_data.current_node = "validar_credentials"
    print("Estado: validado -> validar_credentials")  # Depuración
    return None  # No cambia el nodo aquí

def validar_credentials(state: GraphState):
    user_id = state.user_data.user_id
    incoming_msg = state.incoming_msg

    print(f"Validando credenciales para {user_id} con mensaje: {incoming_msg}")  # Depuración

    # Validar que el mensaje contenga un documento (números) y un correo electrónico (contiene '@')
    parts = incoming_msg.split()
    document = None
    email = None
    for part in parts:
        if part.isdigit():
            document = part
        elif "@" in part:
            email = part

    if document and email:
        # Aquí podrías agregar lógica adicional para verificar en la base de datos si las credenciales son válidas
        state.user_data.credit_number = document
        state.user_data.consultation_received = True
        instruction = "Gracias por proporcionar sus credenciales. Ahora, por favor indíqueme cuál es su consulta para poder ayudarle mejor."
        llm.send_message(user_id, instruction)
        state.user_data.awaiting_response = True
        state.user_data.current_node = "solicitar_consulta"
        print("Estado: validar_credentials -> solicitar_consulta")  # Depuración
    else:
        instruction = "La información proporcionada no es válida. Por favor, asegúrese de incluir su número de documento de identidad y un correo electrónico válido."
        llm.send_message(user_id, instruction)
        state.user_data.awaiting_response = True
        state.user_data.current_node = "validar_credentials"
        print("Estado: validar_credentials -> validar_credentials")  # Depuración
    return None

def solicitar_consulta(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "Por favor, indíqueme cuál es su consulta para poder ayudarle mejor."
    llm.send_message(user_id, instruction)
    state.user_data.awaiting_response = True
    state.user_data.current_node = "procesar_consulta"
    print("Estado: solicitar_consulta -> procesar_consulta")  # Depuración
    return None

def procesar_consulta(state: GraphState):
    user_id = state.user_data.user_id
    incoming_msg = state.incoming_msg

    print(f"Procesando consulta para {user_id}: {incoming_msg}")  # Depuración

    if state.user_data.consultation_received:
        # Inicializar y usar MilvusRetriever para buscar documentos relevantes
        try:
            retriever = MilvusRetriever(documents=[], k=3)
            retriever.init()
            relevant_docs = retriever.invoke(incoming_msg)
            print(f"Documentos relevantes encontrados: {len(relevant_docs)}")  # Depuración
        except Exception as e:
            print(f"Error al usar MilvusRetriever: {e}")
            relevant_docs = []

        if relevant_docs:
            docs_summary = "\n".join([doc.page_content for doc in relevant_docs])
            instruction = (
                f"El usuario ha hecho la siguiente consulta: '{incoming_msg}'. "
                f"Utiliza la información relevante que hemos encontrado en la base de datos:\n{docs_summary}\n"
                f"Por favor, redacta una respuesta informativa, clara y concisa que resuelva la consulta del usuario."
            )
        else:
            instruction = (
                f"El usuario ha hecho la siguiente consulta: '{incoming_msg}'. "
                "Sin embargo, no hemos encontrado información relevante en la base de datos. Por favor, responde al usuario "
                "informando que no pudimos encontrar los datos solicitados y ofrece asistencia adicional."
            )

        llm.send_message(user_id, instruction)

        # Avanzar al nodo de validación de satisfacción
        state.user_data.satisfaction_attempts += 1
        if state.user_data.satisfaction_attempts == 1:
            state.user_data.current_node = "validar_satisfaccion"
            print("Estado: procesar_consulta -> validar_satisfaccion")  # Depuración
        else:
            state.user_data.current_node = "preguntar_cita"
            print("Estado: procesar_consulta -> preguntar_cita")  # Depuración
    else:
        instruction = "Por favor, proporcione su número de documento de identidad y su correo electrónico."
        llm.send_message(user_id, instruction)
        state.user_data.awaiting_response = True
        state.user_data.current_node = "validar_credentials"
        print("Estado: procesar_consulta -> validar_credentials")  # Depuración
    return None

def validar_satisfaccion(state: GraphState):
    user_id = state.user_data.user_id
    instruction = (
        "¿La información proporcionada responde a su consulta de manera satisfactoria? "
        "Si no es así, por favor reestructure su pregunta para que podamos evaluar mejor su solicitud."
    )
    llm.send_message(user_id, instruction)
    state.user_data.awaiting_response = True
    state.user_data.current_node = "validar_respuesta_satisfaccion"
    print("Estado: validar_satisfaccion -> validar_respuesta_satisfaccion")  # Depuración
    return None

def validar_respuesta_satisfaccion(state: GraphState):
    user_id = state.user_data.user_id
    incoming_msg = state.incoming_msg.lower()

    print(f"Validando satisfacción para {user_id}: {incoming_msg}")  # Depuración

    if incoming_msg in ["sí", "si", "yes"]:
        instruction = "¡Gracias por su feedback! Si necesita más ayuda, no dude en contactarnos."
        llm.send_message(user_id, instruction)
        state.user_data.current_node = "end_conversation"
        print("Estado: validar_respuesta_satisfaccion -> end_conversation")  # Depuración
    elif incoming_msg in ["no", "nah", "nope"]:
        instruction = (
            "Lamentamos que la información proporcionada no haya sido suficiente. "
            "¿Desea agendar una cita con uno de nuestros asesores del equipo ZAC para que ellos le resuelvan mejor la duda? "
            "Por favor, responda 'Sí' o 'No'."
        )
        llm.send_message(user_id, instruction)
        state.user_data.appointment_requested = True
        state.user_data.awaiting_response = True
        state.user_data.current_node = "preguntar_cita"
        print("Estado: validar_respuesta_satisfaccion -> preguntar_cita")  # Depuración
    else:
        instruction = "Por favor, responda 'Sí' o 'No' para indicar su satisfacción con la información proporcionada."
        llm.send_message(user_id, instruction)
        state.user_data.awaiting_response = True
        state.user_data.current_node = "validar_respuesta_satisfaccion"
        print("Estado: validar_respuesta_satisfaccion -> validar_respuesta_satisfaccion")  # Depuración
    return None

def preguntar_cita(state: GraphState):
    user_id = state.user_data.user_id
    incoming_msg = state.incoming_msg.lower()

    print(f"Preguntando cita para {user_id}: {incoming_msg}")  # Depuración

    if incoming_msg in ["sí", "si", "yes"]:
        state.user_data.current_node = "mostrar_horarios"
        print("Estado: preguntar_cita -> mostrar_horarios")  # Depuración
    elif incoming_msg in ["no", "nah", "nope"]:
        state.user_data.current_node = "encuesta"
        print("Estado: preguntar_cita -> encuesta")  # Depuración
    else:
        instruction = "Por favor, responda 'Sí' o 'No' para indicar si desea agendar una cita."
        llm.send_message(user_id, instruction)
        state.user_data.awaiting_response = True
        state.user_data.current_node = "preguntar_cita"
        print("Estado: preguntar_cita -> preguntar_cita")  # Depuración
    return None

def mostrar_horarios(state: GraphState):
    user_id = state.user_data.user_id
    now = datetime.now()
    available_slots = []
    start_time = now + timedelta(hours=2)
    start_time = start_time.replace(minute=0, second=0, microsecond=0)
    if start_time.hour < 8:
        start_time = start_time.replace(hour=8)
    elif start_time.hour >= 18:
        start_time = start_time + timedelta(days=1)
        start_time = start_time.replace(hour=8)
    end_time = start_time.replace(hour=18)
    current_time = start_time

    while current_time < end_time:
        if current_time.weekday() < 5:
            slot_str = current_time.strftime('%Y-%m-%d %H:%M')
            available_slots.append(slot_str)
        current_time += timedelta(minutes=20)

    state.user_data.available_slots = available_slots

    instruction = (
        f"Estos son los horarios disponibles para agendar una cita:\n" +
        "\n".join([f"{i+1}. {slot}" for i, slot in enumerate(available_slots)]) +
        "\nPor favor, seleccione uno de estos horarios respondiendo con el número correspondiente."
    )
    llm.send_message(user_id, instruction)
    state.user_data.awaiting_response = True
    state.user_data.current_node = "seleccionar_horario"
    print("Estado: mostrar_horarios -> seleccionar_horario")  # Depuración
    return None

def seleccionar_horario(state: GraphState):
    user_id = state.user_data.user_id
    incoming_msg = state.incoming_msg

    print(f"Seleccionando horario para {user_id}: {incoming_msg}")  # Depuración

    if incoming_msg.isdigit():
        slot_index = int(incoming_msg) - 1
        if 0 <= slot_index < len(state.user_data.available_slots):
            state.user_data.selected_slot = state.user_data.available_slots[slot_index]
            state.user_data.awaiting_slot_selection = False
            state.user_data.appointment_details_step = 0
            state.user_data.awaiting_appointment_details = True
            instruction = "Por favor, proporcione su número de crédito."
            llm.send_message(user_id, instruction)
            state.user_data.current_node = "confirmar_cita"
            print("Estado: seleccionar_horario -> confirmar_cita")  # Depuración
        else:
            instruction = "Selección inválida. Por favor, seleccione un número de la lista de horarios disponibles."
            llm.send_message(user_id, instruction)
            state.user_data.current_node = "seleccionar_horario"
            print("Estado: seleccionar_horario -> seleccionar_horario (válida nuevamente)")  # Depuración
    else:
        instruction = "Por favor, ingrese el número correspondiente al horario que desea."
        llm.send_message(user_id, instruction)
        state.user_data.current_node = "seleccionar_horario"
        print("Estado: seleccionar_horario -> seleccionar_horario (válida nuevamente)")  # Depuración
    return None

def confirmar_cita(state: GraphState):
    user_id = state.user_data.user_id
    user_data = state.user_data
    steps = [
        "Por favor, proporcione su número de crédito.",
        "Ahora, indique su nombre.",
        "Indique su apellido.",
        "Proporcione su número de teléfono.",
        "Indique el motivo de la cita."
    ]

    if user_data.appointment_details_step < len(steps):
        instruction = steps[user_data.appointment_details_step]
        llm.send_message(user_id, instruction)
        user_data.appointment_details_step += 1
        state.user_data.awaiting_response = True
        state.user_data.current_node = "confirmar_cita_detalle"
        print(f"Estado: confirmar_cita -> confirmar_cita_detalle ({user_data.appointment_details_step})")  # Depuración
    else:
        # Finalizar la recopilación de detalles y agendar cita
        state.user_data.current_node = "cita_agendada"
        print("Estado: confirmar_cita -> cita_agendada")  # Depuración
    return None

def confirmar_cita_detalle(state: GraphState):
    user_data = state.user_data
    incoming_msg = state.incoming_msg

    print(f"Confirmando detalle de cita para {user_data.user_id}: {incoming_msg}")  # Depuración

    if user_data.appointment_details_step == 1:
        user_data.credit_number = incoming_msg
    elif user_data.appointment_details_step == 2:
        user_data.first_name = incoming_msg
    elif user_data.appointment_details_step == 3:
        user_data.last_name = incoming_msg
    elif user_data.appointment_details_step == 4:
        user_data.phone_number = incoming_msg
    elif user_data.appointment_details_step == 5:
        user_data.reason = incoming_msg

    if user_data.appointment_details_step < 5:
        # Solicitar el siguiente detalle
        steps = [
            "Por favor, proporcione su número de crédito.",
            "Ahora, indique su nombre.",
            "Indique su apellido.",
            "Proporcione su número de teléfono.",
            "Indique el motivo de la cita."
        ]
        instruction = steps[user_data.appointment_details_step]
        llm.send_message(user_data.user_id, instruction)
        state.user_data.awaiting_response = True
        state.user_data.current_node = "confirmar_cita_detalle"
        print(f"Estado: confirmar_cita_detalle -> confirmar_cita_detalle ({user_data.appointment_details_step})")  # Depuración
    else:
        # Todos los detalles recopilados, agendar cita
        state.user_data.current_node = "cita_agendada"
        print("Estado: confirmar_cita_detalle -> cita_agendada")  # Depuración
    return None

def cita_agendada(state: GraphState):
    user_id = state.user_data.user_id
    save_appointment(state.user_data)

    instruction = (
        f"Su cita ha sido agendada para el {state.user_data.selected_slot}. "
        "Un asesor se pondrá en contacto con usted."
    )
    llm.send_message(user_id, instruction)
    state.user_data.current_node = "end_conversation"
    print("Estado: cita_agendada -> end_conversation")  # Depuración
    return None

def encuesta(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "¿Cómo calificaría la información recibida? Por favor, responda con una cantidad de estrellas (1-5)."
    llm.send_message(user_id, instruction)
    state.user_data.rating_requested = True
    state.user_data.awaiting_response = True
    state.user_data.current_node = "procesar_encuesta"
    print("Estado: encuesta -> procesar_encuesta")  # Depuración
    return None

def procesar_encuesta(state: GraphState):
    user_id = state.user_data.user_id
    incoming_msg = state.incoming_msg

    print(f"Procesando encuesta para {user_id}: {incoming_msg}")  # Depuración

    if incoming_msg.isdigit() and 1 <= int(incoming_msg) <= 5:
        user_data = state.user_data
        user_data.rated = True
        user_data.rating_requested = False
        # Aquí podrías guardar la calificación en una base de datos si lo deseas
        instruction = "¡Gracias por su calificación! Si necesita más ayuda, no dude en contactarnos."
        llm.send_message(user_id, instruction)
        state.user_data.current_node = "end_conversation"
        print("Estado: procesar_encuesta -> end_conversation")  # Depuración
    else:
        instruction = "Por favor, proporcione una calificación válida entre 1 y 5."
        llm.send_message(user_id, instruction)
        state.user_data.current_node = "procesar_encuesta"
        print("Estado: procesar_encuesta -> procesar_encuesta")  # Depuración
    return None

def end_conversation(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "Gracias por su tiempo. La conversación ha finalizado."
    llm.send_message(user_id, instruction)
    state.user_data.conversation_active = False
    print("Estado: end_conversation")  # Depuración
    return None

# Definir el grafo de estados
class StateGraph:
    def __init__(self, state_schema):
        self.state_schema = state_schema
        self.nodes = {}
        self.edges = {}

    def add_node(self, name, func):
        self.nodes[name] = func

    def add_edge(self, from_node, to_node):
        self.edges.setdefault(from_node, []).append(to_node)

# Crear el grafo de estados
graph = StateGraph(state_schema=GraphState)
graph.add_node("validado", validado)
graph.add_node("validar_credentials", validar_credentials)
graph.add_node("solicitar_consulta", solicitar_consulta)
graph.add_node("procesar_consulta", procesar_consulta)
graph.add_node("validar_satisfaccion", validar_satisfaccion)
graph.add_node("validar_respuesta_satisfaccion", validar_respuesta_satisfaccion)
graph.add_node("preguntar_cita", preguntar_cita)
graph.add_node("mostrar_horarios", mostrar_horarios)
graph.add_node("seleccionar_horario", seleccionar_horario)
graph.add_node("confirmar_cita", confirmar_cita)
graph.add_node("confirmar_cita_detalle", confirmar_cita_detalle)
graph.add_node("cita_agendada", cita_agendada)
graph.add_node("encuesta", encuesta)
graph.add_node("procesar_encuesta", procesar_encuesta)
graph.add_node("end_conversation", end_conversation)

# Definir las conexiones entre nodos
graph.add_edge("validado", "validar_credentials")
graph.add_edge("validar_credentials", "solicitar_consulta")
graph.add_edge("solicitar_consulta", "procesar_consulta")
graph.add_edge("procesar_consulta", "validar_satisfaccion")
graph.add_edge("validar_satisfaccion", "validar_respuesta_satisfaccion")
graph.add_edge("validar_respuesta_satisfaccion", "preguntar_cita")
graph.add_edge("preguntar_cita", "mostrar_horarios")
graph.add_edge("preguntar_cita", "encuesta")
graph.add_edge("mostrar_horarios", "seleccionar_horario")
graph.add_edge("seleccionar_horario", "confirmar_cita")
graph.add_edge("confirmar_cita", "confirmar_cita_detalle")
graph.add_edge("confirmar_cita_detalle", "cita_agendada")
graph.add_edge("cita_agendada", "end_conversation")
graph.add_edge("encuesta", "procesar_encuesta")
graph.add_edge("procesar_encuesta", "end_conversation")

# Ejecutar el grafo de estados
def execute_node(graph, current_node_name, state):
    node_function = graph.nodes.get(current_node_name)
    if not node_function:
        print(f"Error: Nodo '{current_node_name}' no encontrado.")
        return None
    node_function(state)
    return state.user_data.current_node

# Manejo del temporizador de inactividad
def reset_timer(user_data):
    with user_data._timer_lock:
        if user_data._timer:
            user_data._timer.cancel()
        user_data.reminders_sent = 0
        user_data._timer = Timer(300, send_reminder, [user_data])  # 5 minutos
        user_data._timer.start()

def send_reminder(user_data):
    with user_data._timer_lock:
        if user_data.conversation_active:
            user_data.reminders_sent += 1
            if user_data.reminders_sent == 1:
                message = "¿Sigues ahí? Recuerda que tras 5 minutos de inactividad se finalizará la asistencia."
                print(f"Assistant: {message}")
                user_data._timer = Timer(300, send_reminder, [user_data])  # 5 minutos adicionales
                user_data._timer.start()
            else:
                message = "La conversación ha finalizado por inactividad."
                print(f"Assistant: {message}")
                user_data.conversation_active = False

# Ejecutar el chatbot
def run_graph(user_id):
    if user_id in user_states:
        user_data = user_states[user_id]
    else:
        user_data = UserData(user_id=user_id)
        user_states[user_id] = user_data

    state = GraphState(user_data=user_data)
    state.user_data.current_node = "validado"  # Inicializar el nodo actual
    execute_node(graph, state.user_data.current_node, state)
    reset_timer(user_data)

    while user_data.conversation_active:
        try:
            if user_data.awaiting_response:
                user_input = input("You: ").strip()
                reset_timer(user_data)
                if not user_input:
                    continue
                state.incoming_msg = user_input
                # Ejecutar el nodo actual con el mensaje del usuario
                current_node = user_data.current_node
                execute_node(graph, current_node, state)
            else:
                # No está esperando respuesta, podría ser un estado final o pausa
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nAssistant: La conversación ha sido interrumpida. Hasta luego.")
            break

    if user_data._timer:
        user_data._timer.cancel()

# Punto de entrada del script
if __name__ == "__main__":
    user_id = "usuario_123"  # Puedes cambiar esto para diferentes usuarios
    init_db()
    run_graph(user_id)
