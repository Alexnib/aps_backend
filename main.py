from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from routers import auth, clienti, cantieri, operai, mezzi, fornitori, rapportini, dashboard, import_pdf, materiali, sal, subappalti, subappaltatori

app = FastAPI(title="APS Light API", description="API per l'app gestionale APS Light")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Come il gestore 422 predefinito di FastAPI, ma senza rimandare indietro nella risposta il valore
# digitato nei campi password (finirebbe in chiaro in log, strumenti di monitoraggio, ecc.).
@app.exception_handler(RequestValidationError)
async def errore_validazione(request: Request, exc: RequestValidationError):
    errori = []
    for errore in exc.errors():
        errore = dict(errore)
        if any("password" in str(parte) for parte in errore.get("loc", ())):
            errore.pop("input", None)
        errori.append(errore)
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errori)})


app.include_router(auth.router)
app.include_router(clienti.router)
app.include_router(cantieri.router)
app.include_router(operai.router)
app.include_router(mezzi.router)
app.include_router(fornitori.router)
app.include_router(rapportini.router)
app.include_router(dashboard.router)
app.include_router(import_pdf.router)
app.include_router(materiali.router)
app.include_router(sal.router)
app.include_router(subappalti.router)
app.include_router(subappaltatori.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to APS Light API"}
