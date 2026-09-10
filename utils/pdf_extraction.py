import base64

from anthropic import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from fastapi import HTTPException

from .anthropic_client import get_anthropic_client

MODEL = "claude-sonnet-5"
# Tetto massimo supportato in output da claude-sonnet-5. Si paga solo per i token
# effettivamente generati (non per il tetto stesso), quindi qui conviene stare al
# massimo per non troncare i computi metrici piu' lunghi (es. 33 pagine, 200+ voci).
MAX_TOKENS = 128000

# Prezzi ufficiali per milione di token (claude-sonnet-5)
PREZZO_INPUT_PER_MTOK = 2.0
PREZZO_OUTPUT_PER_MTOK = 10.0


COMPUTO_TOOL = {
    "name": "estrai_voci_computo",
    "description": "Estrae tutte le voci di un computo metrico estimativo da un documento PDF.",
    "input_schema": {
        "type": "object",
        "properties": {
            "voci": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "n_voce": {
                            "type": "string",
                            "description": "Numero identificativo della voce (es. '1', '2.1')",
                        },
                        "codice_tariffa": {
                            "type": "string",
                            "description": "Codice di tariffa/prezzario della voce, se presente (es. 'E.002.002'). Stringa vuota se assente.",
                        },
                        "descrizione_lavorazione": {
                            "type": "string",
                            "description": "Descrizione testuale completa della lavorazione",
                        },
                        "unita_misura": {
                            "type": "string",
                            "description": "Unita' di misura (es. mq, mc, kg, cad, ml)",
                        },
                        "quantita_prevista": {
                            "type": "number",
                            "description": "Quantita' numerica prevista per la voce",
                        },
                        "importo_unitario": {
                            "type": "number",
                            "description": "Prezzo unitario in euro",
                        },
                        "importo_totale": {
                            "type": "number",
                            "description": "Importo totale della voce in euro (quantita' * prezzo unitario)",
                        },
                    },
                    "required": [
                        "n_voce",
                        "codice_tariffa",
                        "descrizione_lavorazione",
                        "unita_misura",
                        "quantita_prevista",
                        "importo_unitario",
                        "importo_totale",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["voci"],
        "additionalProperties": False,
    },
}

DDT_TOOL = {
    "name": "estrai_ddt",
    "description": "Estrae i dati strutturati da uno o piu' Documenti Di Trasporto (DDT) contenuti in un PDF.",
    "input_schema": {
        "type": "object",
        "properties": {
            "documenti": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fornitore": {
                            "type": "string",
                            "description": "Ragione sociale del fornitore/mittente che emette il DDT",
                        },
                        "numero_documento": {
                            "type": "string",
                            "description": "Numero del documento di trasporto",
                        },
                        "data_ddt": {
                            "type": "string",
                            "description": "Data del documento in formato YYYY-MM-DD",
                        },
                        "destinatario_testo": {
                            "type": "string",
                            "description": "Testo completo del destinatario/luogo di consegna cosi' come scritto nel documento (ragione sociale, cantiere, indirizzo)",
                        },
                        "righe": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "codice": {
                                        "type": "string",
                                        "description": "Codice articolo, se presente. Stringa vuota se assente.",
                                    },
                                    "descrizione": {
                                        "type": "string",
                                        "description": "Descrizione del materiale",
                                    },
                                    "unita_misura": {
                                        "type": "string",
                                        "description": "Unita' di misura (es. mq, mc, kg, pz, ml)",
                                    },
                                    "quantita": {
                                        "type": "number",
                                        "description": "Quantita' consegnata",
                                    },
                                },
                                "required": ["codice", "descrizione", "unita_misura", "quantita"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": [
                        "fornitore",
                        "numero_documento",
                        "data_ddt",
                        "destinatario_testo",
                        "righe",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["documenti"],
        "additionalProperties": False,
    },
}


PREVENTIVO_TOOL = {
    "name": "estrai_preventivo_fornitore",
    "description": "Estrae le voci di un preventivo/listino prezzi di un fornitore da un documento PDF.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fornitore": {
                "type": "string",
                "description": "Ragione sociale del fornitore che ha emesso il preventivo/listino. Stringa vuota se non identificabile.",
            },
            "data_offerta": {
                "type": "string",
                "description": "Data del preventivo/offerta in formato YYYY-MM-DD, se presente nel documento. Stringa vuota se assente.",
            },
            "voci": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "codice_articolo": {
                            "type": "string",
                            "description": "Codice articolo del fornitore, se presente. Stringa vuota se assente.",
                        },
                        "descrizione": {
                            "type": "string",
                            "description": "Descrizione del materiale/articolo",
                        },
                        "unita_misura": {
                            "type": "string",
                            "description": "Unita' di misura (es. mq, mc, kg, pz, ml)",
                        },
                        "prezzo_unitario": {
                            "type": "number",
                            "description": "Prezzo unitario in euro. Se il documento riporta solo un importo totale e una quantita', calcolalo dividendo l'uno per l'altra.",
                        },
                        "quantita": {
                            "type": "number",
                            "description": "Quantita' di riferimento della riga, se presente nel documento (utile per verificare l'importo). 1 se non indicata esplicitamente.",
                        },
                        "importo": {
                            "type": "number",
                            "description": "Importo totale della riga in euro (quantita' * prezzo unitario), se presente esplicitamente nel documento. 0 se assente.",
                        },
                    },
                    "required": [
                        "codice_articolo",
                        "descrizione",
                        "unita_misura",
                        "prezzo_unitario",
                        "quantita",
                        "importo",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["fornitore", "data_offerta", "voci"],
        "additionalProperties": False,
    },
}


def _pdf_document_block(pdf_bytes: bytes) -> dict:
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.b64encode(pdf_bytes).decode("utf-8"),
        },
    }


def _calcola_costo(usage) -> float:
    costo_input = (usage.input_tokens / 1_000_000) * PREZZO_INPUT_PER_MTOK
    costo_output = (usage.output_tokens / 1_000_000) * PREZZO_OUTPUT_PER_MTOK
    return round(costo_input + costo_output, 4)


def _esegui_estrazione(pdf_bytes: bytes, filename: str, tool: dict, prompt: str) -> dict:
    client = get_anthropic_client()

    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[
                {
                    "role": "user",
                    "content": [
                        _pdf_document_block(pdf_bytes),
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        ) as stream:
            message = stream.get_final_message()
    except AuthenticationError:
        raise HTTPException(
            status_code=500,
            detail="Chiave API Anthropic non valida o non configurata. Contattare l'amministratore.",
        )
    except PermissionDeniedError:
        raise HTTPException(
            status_code=500,
            detail="Accesso negato dall'API Anthropic. Verificare i permessi della chiave API.",
        )
    except RateLimitError:
        raise HTTPException(
            status_code=429,
            detail="Limite di richieste all'API Anthropic superato. Riprovare tra qualche minuto.",
        )
    except APIConnectionError:
        raise HTTPException(
            status_code=502,
            detail="Impossibile contattare il servizio di estrazione. Verificare la connessione internet e riprovare.",
        )
    except APIStatusError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Errore del servizio di estrazione (Anthropic): {e.message}",
        )

    if message.stop_reason == "refusal":
        raise HTTPException(
            status_code=422,
            detail=f"Il modello ha rifiutato di elaborare '{filename}'. Verificare che il PDF sia leggibile e contenga il documento atteso.",
        )

    if message.stop_reason == "max_tokens":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Il documento '{filename}' e' troppo lungo per essere estratto in un'unica richiesta "
                "(l'output e' stato troncato). Suddividere il PDF in parti piu' piccole e importarle separatamente."
            ),
        )

    tool_use_block = next(
        (block for block in message.content if block.type == "tool_use"), None
    )
    if tool_use_block is None:
        raise HTTPException(
            status_code=422,
            detail=f"Nessun dato strutturato estratto da '{filename}'. Il documento potrebbe non essere leggibile.",
        )

    return {
        "data": tool_use_block.input,
        "usage": {
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            "costo_stimato_usd": _calcola_costo(message.usage),
        },
    }


def estrai_computo_metrico(pdf_bytes: bytes, filename: str) -> dict:
    prompt = (
        "Analizza questo computo metrico estimativo ed estrai TUTTE le voci presenti nel documento, "
        "senza ometterne nessuna, usando lo strumento fornito. Mantieni l'ordine originale del documento. "
        "Se un valore numerico non e' leggibile con certezza, riporta la stima piu' plausibile in base al calcolo "
        "quantita' * prezzo unitario = importo totale."
    )
    return _esegui_estrazione(pdf_bytes, filename, COMPUTO_TOOL, prompt)


def estrai_preventivo_fornitore(pdf_bytes: bytes, filename: str) -> dict:
    prompt = (
        "Analizza questo preventivo/listino prezzi di un fornitore ed estrai TUTTE le voci/articoli "
        "presenti (codice, descrizione, unita' di misura, prezzo unitario), senza ometterne nessuna, "
        "usando lo strumento fornito. Mantieni l'ordine originale del documento. Se il prezzo unitario "
        "non e' indicato esplicitamente ma e' ricavabile da importo totale / quantita', calcolalo."
    )
    return _esegui_estrazione(pdf_bytes, filename, PREVENTIVO_TOOL, prompt)


def estrai_ddt(pdf_bytes: bytes, filename: str) -> dict:
    prompt = (
        "Analizza questo documento PDF: puo' contenere uno o piu' Documenti Di Trasporto (DDT). "
        "Estrai ogni DDT presente come elemento separato nell'array 'documenti', con tutte le relative righe di materiale, "
        "usando lo strumento fornito. Non unire DDT diversi anche se hanno lo stesso fornitore."
    )
    return _esegui_estrazione(pdf_bytes, filename, DDT_TOOL, prompt)
