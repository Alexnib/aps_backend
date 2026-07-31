from fastapi import HTTPException


def raise_db_error(e: Exception):
    """Translate raw Postgres/PostgREST errors into clean HTTP responses."""
    msg = str(e)
    if "foreign key" in msg.lower() or "23503" in msg:
        raise HTTPException(
            status_code=409,
            detail="Impossibile eliminare: elemento collegato ad altri dati esistenti (es. rapportini o cantieri)."
        )
    raise HTTPException(status_code=400, detail=msg)
