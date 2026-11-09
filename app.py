import os
import json

import requests
import streamlit as st


API_URL = os.getenv("RAG_API_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="RAG Local", layout="wide")
st.title("Sistema RAG Local")
tab_chat, tab_documents = st.tabs(["Chat", "Documentos"])

with tab_chat:
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if question := st.chat_input("Pregunta sobre los documentos indexados"):
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            try:
                response = requests.post(
                    f"{API_URL}/ask",
                    json={"question": question},
                    timeout=180,
                )
                response.raise_for_status()
                result = response.json()
                st.markdown(result["respuesta"])
                for source in result["fuentes"]:
                    st.caption(
                        f"{source['filename']} — fragmento {source['chunk_position']} "
                        f"(relevancia: {source['score']:.3f})"
                    )
                st.session_state.messages.append(
                    {"role": "assistant", "content": result["respuesta"]}
                )
            except (requests.RequestException, KeyError, ValueError) as error:
                st.error(f"No fue posible consultar la API: {error}")

with tab_documents:
    uploaded_file = st.file_uploader("Selecciona un documento PDF", type=["pdf"])
    metadata_text = st.text_input("Metadatos JSON", value="{}")
    if st.button("Cargar documento", disabled=uploaded_file is None):
        try:
            metadata = json.loads(metadata_text)
            if not isinstance(metadata, dict):
                raise ValueError("Los metadatos deben ser un objeto JSON.")
            response = requests.post(
                f"{API_URL}/documents/upload",
                files={
                    "file": (
                        uploaded_file.name,
                        uploaded_file.getvalue(),
                        uploaded_file.type or "application/pdf",
                    )
                },
                data={"metadata": json.dumps(metadata)},
                timeout=180,
            )
            response.raise_for_status()
            st.session_state.document_id = response.json()["document_id"]
            st.success(f"Documento cargado: {st.session_state.document_id}")
        except (json.JSONDecodeError, requests.RequestException, ValueError) as error:
            st.error(f"No fue posible cargar el documento: {error}")

    document_id = st.session_state.get("document_id")
    if document_id and st.button("Procesar e indexar"):
        try:
            response = requests.post(
                f"{API_URL}/documents/ingest",
                json={"document_id": document_id},
                timeout=600,
            )
            response.raise_for_status()
            result = response.json()
            st.success(f"Fragmentos indexados: {result['indexed_chunks']}")
        except (requests.RequestException, KeyError, ValueError) as error:
            st.error(f"No fue posible procesar el documento: {error}")

    if st.button("Actualizar documentos"):
        try:
            response = requests.get(f"{API_URL}/documents", timeout=30)
            response.raise_for_status()
            st.dataframe(response.json()["documents"], use_container_width=True)
        except (requests.RequestException, KeyError, ValueError) as error:
            st.error(f"No fue posible obtener los documentos: {error}")
