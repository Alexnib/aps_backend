from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import auth, clienti, cantieri, operai, mezzi, fornitori, rapportini, dashboard, import_pdf, materiali

app = FastAPI(title="APS Light API", description="API per l'app gestionale APS Light")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.get("/")
def read_root():
    return {"message": "Welcome to APS Light API"}
