from pydantic import BaseModel, Field, field_validator, PrivateAttr
from typing import Optional, List
from threading import Timer, Lock
import time
import os
from datetime import datetime, timedelta
import sqlite3
from milvus_model.hybrid import BGEM3EmbeddingFunction
from MilvusRetriever import MilvusRetriever
from dotenv import load_dotenv
from langchain_groq import ChatGroq
load_dotenv()

class ChatGroqWrapper:
    def __init__(self, groq_api_key, model_name):
        self.llm = ChatGroq(api_key=os.environ['GROQ_API_KEY'], model_name=model_name)
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
        print(f"Assistant: {assistant_reply}")
        return assistant_reply

llm = ChatGroqWrapper(groq_api_key=os.environ['GROQ_API_KEY'], model_name="llama3-70b-8192")

class UserData(BaseModel):
    user_id: str
    start_time: float = Field(default_factory=time.time)
    prompted: bool = False
    completed: bool = False
    rated: bool = False
    rating_requested: bool = False
    comment_requested: bool = False
    appointment_requested: bool = False
    appointment_confirmed: bool = False
    conversation_active: bool = True
    reminders_sent: int = 0
    credit_number: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone_number: Optional[str] = None
    selected_slot: Optional[str] = None
    reason: Optional[str] = None
    available_slots: List[str] = []
    awaiting_slot_selection: bool = False
    awaiting_appointment_details: bool = False
    appointment_details_step: int = 0
    satisfaction_attempts: int = 0  # Para controlar los intentos de satisfacción

    _timer_lock: Lock = PrivateAttr(default_factory=Lock)
    _timer: Optional[Timer] = PrivateAttr(default=None)

class GraphState(BaseModel):
    user_data: UserData
    incoming_msg: Optional[str] = None
    awaiting_response: bool = False

    @field_validator('incoming_msg')
    def validate_message(cls, v):
        return v

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

def validado(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "Por favor, proporcione su documento y correo electrónico."
    llm.send_message(user_id, instruction)
    state.user_data.prompted = True
    state.awaiting_response = True
    return None

def solicitar_consulta(state: GraphState):
    user_id = state.user_data.user_id
    instruction = (
        "Gracias por proporcionar su información. "
        "Ahora, por favor indíqueme cuál es su consulta para poder ayudarle mejor."
    )
    llm.send_message(user_id, instruction)
    state.awaiting_response = True
    return None

def informacion(state: GraphState):
    user_id = state.user_data.user_id
    incoming_msg = state.incoming_msg

    if state.user_data.completed:
        retriever = MilvusRetriever(documents=[], k=3)
        retriever.init()
        relevant_docs = retriever.invoke(incoming_msg)

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

        # Verificamos si el usuario está satisfecho
        state.user_data.satisfaction_attempts += 1

        if state.user_data.satisfaction_attempts == 1:
            return "validar_satisfaccion"
        else:
            # Si es la segunda vez que no está satisfecho, ofrecer cita
            return "preguntar_cita"
    else:
        instruction = "Por favor, proporcione su documento y correo electrónico para continuar."
        llm.send_message(user_id, instruction)
        return None

def validar_satisfaccion(state: GraphState):
    user_id = state.user_data.user_id
    instruction = (
        "¿La información proporcionada responde a su consulta de manera satisfactoria? "
        "Si no es así, por favor reestructure su pregunta para que podamos evaluar mejor su solicitud."
    )
    llm.send_message(user_id, instruction)
    return None

def preguntar_cita(state: GraphState):
    user_id = state.user_data.user_id
    instruction = (
        "Hemos intentado resolver su consulta, pero entendemos que aún no está satisfecho. "
        "¿Desea agendar una cita con uno de nuestros asesores del equipo ZAC para que ellos le resuelvan mejor la duda? "
        "Por favor, responda 'Sí' o 'No'."
    )
    llm.send_message(user_id, instruction)
    state.user_data.appointment_requested = True
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
        f"Estos son los horarios disponibles para agendar una cita:\n"
        f"{', '.join(available_slots)}\n"
        "Por favor, seleccione uno de estos horarios respondiendo con el número correspondiente."
    )
    llm.send_message(user_id, instruction)
    state.user_data.awaiting_slot_selection = True
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
    else:
        user_data.awaiting_appointment_details = False
        execute_graph(graph, "cita_agendada", state)
    return None

def cita_agendada(state: GraphState):
    user_id = state.user_data.user_id
    save_appointment(state.user_data)

    instruction = (
        f"Su cita ha sido agendada para el {state.user_data.selected_slot}. Un asesor se pondrá en contacto con usted."
    )
    llm.send_message(user_id, instruction)
    return "end_conversation"

def encuesta(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "¿Cómo calificaría la información recibida? Por favor, responda con una cantidad de estrellas (1-5)."
    llm.send_message(user_id, instruction)
    state.user_data.rating_requested = True
    return None

def end_conversation(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "Gracias por su tiempo. La conversación ha finalizado."
    llm.send_message(user_id, instruction)
    state.user_data.conversation_active = False

class StateGraph:
    def __init__(self, state_schema):
        self.state_schema = state_schema
        self.nodes = {}
        self.edges = {}

    def add_node(self, name, func):
        self.nodes[name] = func

    def add_edge(self, from_node, to_node):
        self.edges.setdefault(from_node, []).append(to_node)

graph = StateGraph(state_schema=GraphState)
graph.add_node("start", validado)
graph.add_node("solicitar_consulta", solicitar_consulta)
graph.add_node("informacion", informacion)
graph.add_node("validar_satisfaccion", validar_satisfaccion)
graph.add_node("preguntar_cita", preguntar_cita)
graph.add_node("mostrar_horarios", mostrar_horarios)
graph.add_node("confirmar_cita", confirmar_cita)
graph.add_node("cita_agendada", cita_agendada)
graph.add_node("encuesta", encuesta)
graph.add_node("end_conversation", end_conversation)

graph.add_edge("start", "solicitar_consulta")
graph.add_edge("solicitar_consulta", "informacion")
graph.add_edge("informacion", "validar_satisfaccion")
graph.add_edge("validar_satisfaccion", "preguntar_cita")
graph.add_edge("preguntar_cita", "mostrar_horarios")
graph.add_edge("mostrar_horarios", "confirmar_cita")
graph.add_edge("confirmar_cita", "cita_agendada")
graph.add_edge("cita_agendada", "end_conversation")
graph.add_edge("preguntar_cita", "encuesta")
graph.add_edge("encuesta", "end_conversation")

def execute_graph(graph, current_node_name, state):
    while current_node_name:
        node_function = graph.nodes.get(current_node_name)
        if not node_function:
            break
        next_node_name = node_function(state)
        if next_node_name:
            if next_node_name in graph.edges.get(current_node_name, []):
                current_node_name = next_node_name
            else:
                break
        else:
            break

def reset_timer(user_data):
    with user_data._timer_lock:
        if user_data._timer:
            user_data._timer.cancel()
        user_data.reminders_sent = 0
        user_data._timer = Timer(120, send_reminder, [user_data])
        user_data._timer.start()

def send_reminder(user_data):
    with user_data._timer_lock:
        if user_data.conversation_active:
            user_data.reminders_sent += 1
            if user_data.reminders_sent < 2:
                message = "¿Sigues ahí? Recuerda que tras 5 minutos de inactividad se finalizará la asistencia."
                print(f"Assistant: {message}")
                user_data._timer = Timer(120, send_reminder, [user_data])
                user_data._timer.start()
            else:
                message = "La conversación ha finalizado por inactividad."
                print(f"Assistant: {message}")
                user_data.conversation_active = False

def run_graph(user_id):
    if user_id in user_states:
        user_data = user_states[user_id]
    else:
        user_data = UserData(user_id=user_id)
        user_states[user_id] = user_data

    state = GraphState(user_data=user_data)
    execute_graph(graph, "start", state)
    reset_timer(user_data)

    while user_data.conversation_active:
        try:
            user_input = input("You: ")
            reset_timer(user_data)
            if not user_input.strip():
                continue
            state.incoming_msg = user_input

            if state.awaiting_response:
                if "@" in user_input and any(char.isdigit() for char in user_input):
                    user_data.completed = True
                    state.awaiting_response = False
                    execute_graph(graph, "solicitar_consulta", state)
                else:
                    instruction = "Por favor, proporcione la información solicitada."
                    llm.send_message(user_id, instruction)
            elif user_data.appointment_requested:
                if user_input.lower() in ["sí", "si", "yes"]:
                    user_data.appointment_requested = False
                    execute_graph(graph, "mostrar_horarios", state)
                elif user_input.lower() in ["no"]:
                    user_data.appointment_requested = False
                    execute_graph(graph, "encuesta", state)
                else:
                    instruction = "Por favor, responda 'Sí' o 'No'. ¿Desea agendar una cita?"
                    llm.send_message(user_id, instruction)
            elif user_data.awaiting_slot_selection:
                if user_input.strip().isdigit():
                    slot_index = int(user_input.strip()) - 1
                    if 0 <= slot_index < len(user_data.available_slots):
                        user_data.selected_slot = user_data.available_slots[slot_index]
                        user_data.awaiting_slot_selection = False
                        user_data.appointment_details_step = 0
                        user_data.awaiting_appointment_details = True
                        execute_graph(graph, "confirmar_cita", state)
                    else:
                        instruction = "Selección inválida. Por favor, seleccione un número de la lista de horarios disponibles."
                        llm.send_message(user_id, instruction)
                else:
                    instruction = "Por favor, ingrese el número correspondiente al horario que desea."
                    llm.send_message(user_id, instruction)
            elif user_data.awaiting_appointment_details:
                if user_data.appointment_details_step == 0:
                    user_data.credit_number = user_input.strip()
                elif user_data.appointment_details_step == 1:
                    user_data.first_name = user_input.strip()
                elif user_data.appointment_details_step == 2:
                    user_data.last_name = user_input.strip()
                elif user_data.appointment_details_step == 3:
                    user_data.phone_number = user_input.strip()
                elif user_data.appointment_details_step == 4:
                    user_data.reason = user_input.strip()
                user_data.appointment_details_step += 1
                if user_data.appointment_details_step < 5:
                    execute_graph(graph, "confirmar_cita", state)
                else:
                    user_data.awaiting_appointment_details = False
                    execute_graph(graph, "cita_agendada", state)
            elif user_data.rating_requested:
                if user_input.strip().isdigit() and 1 <= int(user_input.strip()) <= 5:
                    user_data.rated = True
                    user_data.rating_requested = False
                    execute_graph(graph, "end_conversation", state)
                else:
                    instruction = "Por favor, proporcione una calificación válida entre 1 y 5."
                    llm.send_message(user_id, instruction)
            else:
                instruction = "Por favor, espere a que se le solicite información."
                llm.send_message(user_id, instruction)
        except KeyboardInterrupt:
            break

    if user_data._timer:
        user_data._timer.cancel()

if __name__ == "__main__":
    user_id = "usuario_123"
    init_db()
    run_graph(user_id)
