from pydantic import BaseModel, Field, field_validator, PrivateAttr
from typing import Optional
from threading import Timer, Lock
import time
import os
from getpass import getpass

from langchain_groq import ChatGroq

if "GROQ_API_KEY" not in os.environ:
    os.environ["GROQ_API_KEY"] = getpass("llmgsk_6QCRZZGxVnJEd9PVrxmfWGdyb3FYLMhr7XESbMf6LJeElvRKiy0C")

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
    conversation_active: bool = True
    reminders_sent: int = 0

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

def validado(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "Por favor, proporcione su documento y correo electrónico."
    llm.send_message(user_id, instruction)
    state.user_data.prompted = True
    state.awaiting_response = True
    return None

def informacion(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "Gracias por proporcionar su información. Ahora le proporcionaremos la información solicitada."
    llm.send_message(user_id, instruction)
    return "encuesta"

def encuesta(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "¿Cómo calificaría la información recibida? Por favor, responda con una cantidad de estrellas (1-5)."
    llm.send_message(user_id, instruction)
    state.user_data.rating_requested = True
    return None

def comentario(state: GraphState):
    user_id = state.user_data.user_id
    instruction = "Por favor, déjenos un comentario sobre su experiencia."
    llm.send_message(user_id, instruction)
    state.user_data.comment_requested = True
    return None

def procesar_comentario(state: GraphState):
    user_id = state.user_data.user_id
    comment = state.incoming_msg
    sentiment_instruction = (
        f"Analice el siguiente comentario y determine si es 'Positivo', 'Negativo' o 'Neutro'. "
        f"Su respuesta debe ser solo una palabra.\n\nComentario: {comment}"
    )
    sentiment_response = llm.send_message(user_id, sentiment_instruction)
    sentiment = sentiment_response.strip().capitalize()
    if 'Positivo' in sentiment:
        response_message = "¡Gracias por su comentario positivo! Nos alegra que haya tenido una buena experiencia."
    elif 'Negativo' in sentiment:
        response_message = "Lamentamos que haya tenido una mala experiencia. Agradecemos sus comentarios y trabajaremos para mejorar."
    else:
        response_message = "Gracias por sus comentarios. Seguiremos esforzándonos para brindarle un mejor servicio."
    llm.send_message(user_id, response_message)
    return "end_conversation"

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
graph.add_node("informacion", informacion)
graph.add_node("encuesta", encuesta)
graph.add_node("comentario", comentario)
graph.add_node("procesar_comentario", procesar_comentario)
graph.add_node("end_conversation", end_conversation)
graph.add_edge("start", "informacion")
graph.add_edge("informacion", "encuesta")
graph.add_edge("encuesta", "comentario")
graph.add_edge("comentario", "procesar_comentario")
graph.add_edge("procesar_comentario", "end_conversation")

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
            user_input = input("Vos: ")
            reset_timer(user_data)
            if not user_input.strip():
                continue
            state.incoming_msg = user_input

            if state.awaiting_response:
                if "@" in user_input and any(char.isdigit() for char in user_input):
                    user_data.completed = True
                    state.awaiting_response = False
                    execute_graph(graph, "informacion", state)
                else:
                    instruction = "Por favor, proporcione la información solicitada."
                    llm.send_message(user_id, instruction)
            elif user_data.rating_requested:
                if user_input.strip().isdigit() and 1 <= int(user_input.strip()) <= 5:
                    user_data.rated = True
                    user_data.rating_requested = False
                    execute_graph(graph, "comentario", state)
                else:
                    instruction = "Por favor, proporcione una calificación válida entre 1 y 5."
                    llm.send_message(user_id, instruction)
            elif user_data.comment_requested:
                user_data.comment_requested = False
                state.incoming_msg = user_input
                execute_graph(graph, "procesar_comentario", state)
            else:
                instruction = "Por favor, espere a que se le solicite información."
                llm.send_message(user_id, instruction)
        except KeyboardInterrupt:
            break

    if user_data._timer:
        user_data._timer.cancel()

def visualize_graph(graph):
    import networkx as nx
    import matplotlib.pyplot as plt

    G = nx.DiGraph()
    for node in graph.nodes:
        G.add_node(node)
    for from_node, to_nodes in graph.edges.items():
        for to_node in to_nodes:
            G.add_edge(from_node, to_node)

    plt.figure(figsize=(8,6))
    pos = nx.spring_layout(G)
    nx.draw(G, pos, with_labels=True, node_color='lightblue', node_size=1500, arrowstyle='->', arrowsize=20)
    plt.title("State Graph")
    plt.savefig("state_graph.png")
    plt.show()

if __name__ == "__main__":
    user_id = "usuario_123"
    #visualize_graph(graph)
    run_graph(user_id)
