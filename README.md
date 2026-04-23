# Auditor Forense de Actas — Elecciones Generales Perú 2026

> **Herramienta de auditoría ciudadana** para contrastar los datos publicados por la ONPE en su portal oficial con la información física contenida en los PDFs de las actas electorales.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/flask-2.3%2B-lightgrey)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ¿Qué hace esta herramienta?

El sistema descarga las actas electorales PDF de la API pública de la ONPE, aplica tres métodos de extracción de datos (OCR local, Google Document AI Enterprise y Gemini Flash 2.5) y compara los resultados con los valores digitalizados oficiales. Adicionalmente realiza un análisis forense del PDF para detectar si fue generado artificialmente o es un escán físico auténtico.

### Flujo principal

```
Mesa electoral (código)
       │
       ▼
  API ONPE ──────────► datos oficiales digitalizados
       │
       ▼
   PDF del acta
       │
       ├──► OCR Local (MNIST CNN + Tesseract) ──────┐
       ├──► Google Document AI Enterprise OCR ───────┤──► Comparación + Discrepancias
       └──► Gemini Flash 2.5 (modo híbrido IA) ──────┘
       │
       ▼
  Análisis Forense PDF
  (Tipo A-E: autenticidad del documento)
       │
       ▼
  Detección de Anomalías
  (One-Class SVM sobre el universo de actas procesadas)
```

---

## Características

| Módulo | Descripción |
|---|---|
| **OCR Local** | CNN entrenada en MNIST (PyTorch) para dígitos manuscritos + Tesseract para texto |
| **Google Document AI** | Enterprise OCR con Custom Extractor, extrae votos y metadatos de acta |
| **Gemini IA Híbrido** | Gemini Flash 2.5 arbitra cuando OCR local y Google discrepan |
| **Análisis Forense** | Detecta actualizaciones incrementales, dígitos amarillos (MIC/TDM), histograma, metadatos, clasificación A-E |
| **Detección de Anomalías** | One-Class SVM entrenado sobre todas las actas procesadas, detecta outliers estadísticos |
| **Barrido Masivo** | Consulta rangos de mesas (000001–999999), clasifica estados, genera reportes CSV/JSON |
| **Comparador** | Cruza datos API ONPE vs OCR por partido, normaliza tildes, calcula discrepancias |

---

## Requisitos

### Software

| Componente | Versión mínima | Notas |
|---|---|---|
| Python | 3.10 | |
| Tesseract OCR | 5.x | Con paquete de idioma `spa` (Spanish) |
| PyTorch | 2.0+ | Solo CPU, el modelo MNIST es pequeño |

**Instalar Tesseract (Windows):**
1. Descargar instalador desde [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
2. Durante la instalación, seleccionar el paquete de idioma **Spanish (spa)**
3. Asegurarse de que `C:\Program Files\Tesseract-OCR\` quede en el PATH

### Dependencias Python

```
flask>=2.3.0
requests>=2.31.0
PyMuPDF>=1.23.0
pytesseract>=0.3.10
Pillow>=10.0.0
numpy>=1.24.0
pikepdf>=8.0.0
scipy>=1.10.0
scikit-learn>=1.4.0
torch>=2.0.0          # solo CPU
google-cloud-documentai>=2.20.0   # opcional
google-auth>=2.28.0               # opcional
google-generativeai>=0.7.0        # opcional
```

---

## Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/TU_USUARIO/auditor-actas-onpe-2026.git
cd auditor-actas-onpe-2026

# 2. Crear entorno virtual
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/macOS

# 3. Instalar dependencias
pip install -r requirements.txt

# ── O usar el script de instalación (Windows) ──
install.bat
```

### Modelo MNIST

El archivo `data/mnist_model.pt` contiene los pesos de la CNN preentrenada. Si no existe, el sistema lo entrena automáticamente al primer uso (requiere ~5 min y descarga el dataset MNIST, ~11 MB).

---

## Configuración

Copiar `.env.example` a `.env` y completar las variables:

```bash
cp .env.example .env
```

### Variables de entorno

| Variable | Requerida | Descripción |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | Solo Google OCR | ID del proyecto GCP |
| `DOCUMENTAI_LOCATION` | Solo Google OCR | Región del procesador (`us` o `eu`) |
| `DOCUMENTAI_PROCESSOR_ID` | Solo Google OCR | ID del procesador Document AI |
| `GOOGLE_APPLICATION_CREDENTIALS` | Solo Google OCR (fallback) | Ruta al JSON de cuenta de servicio |
| `GEMINI_API_KEY` | Solo modo híbrido IA | API Key de Google AI Studio |

> **Nota:** El OCR local (MNIST + Tesseract) funciona completamente offline, sin ninguna clave de API.

#### Autenticación Google Cloud (recomendada)

```bash
# Instalar gcloud CLI: https://cloud.google.com/sdk/docs/install
gcloud auth login
gcloud auth application-default login
gcloud config set project TU_PROYECTO_ID
```

---

## Uso

### Iniciar el servidor

```bash
python app.py
# o en Windows:
run.bat
```

Abrir [http://localhost:5000](http://localhost:5000)

### Interfaz web

**Pestaña "Analizar Mesa"**
1. Ingresar el código de mesa (6 dígitos, p. ej. `054938`)
2. Seleccionar el tipo de elección (Presidencial, Parlamento Andino, Diputados, etc.)
3. Elegir el motor OCR: Local, Google Enterprise o Híbrido IA
4. Ver la tabla comparativa API vs OCR y el análisis forense

**Pestaña "Barrido Masivo"**
- Define un rango de mesas y cuántos hilos usar
- El sistema consulta la API y clasifica cada mesa:
  - `EXISTE_CON_PDF` — tiene acta descargable
  - `EXISTE_SIN_PDF` — registrada pero sin PDF
  - `PARA_JEE` — enviada al Jurado Electoral Especial
  - `OBSERVADA` — acta observada/impugnada
  - `NO_EXISTE` — código no encontrado en la API

**Pestaña "Anomalías"**
- Entrena o actualiza el modelo One-Class SVM con las actas procesadas
- Lista las actas con mayor score de anomalía estadística

### API REST

```http
GET /api/buscar_mesa?codigo_mesa=054938
GET /api/acta_detalle/<acta_id>
POST /api/procesar_ocr
POST /api/analisis_forense
GET  /api/comparar/<acta_id>
GET  /api/barrido/estado
POST /api/barrido/iniciar
POST /api/barrido/detener
GET  /api/anomalias/detectar
```

---

## Estructura del proyecto

```
auditor-actas-onpe-2026/
│
├── app.py                   # Servidor Flask, rutas REST y UI
├── config.py                # Configuración global, endpoints ONPE, colores
├── requirements.txt         # Dependencias Python
├── install.bat              # Instalador Windows
├── run.bat                  # Script de arranque Windows
│
├── modules/
│   ├── api_client.py        # Cliente de la API pública ONPE
│   ├── downloader.py        # Descarga y caché de PDFs
│   ├── ocr_processor.py     # OCR local: detección de celdas + Tesseract
│   ├── digit_classifier.py  # CNN MNIST para dígitos manuscritos
│   ├── google_ocr.py        # Google Document AI Enterprise OCR
│   ├── gemini_client.py     # Gemini Flash 2.5 (modo híbrido IA)
│   ├── comparator.py        # Contraste API ONPE vs OCR
│   ├── forensics.py         # Análisis forense PDF (clasificación A-E)
│   ├── anomaly_detector.py  # One-Class SVM sobre el universo de actas
│   ├── automation.py        # Barrido masivo de mesas
│   ├── database.py          # Capa SQLite (actas, resultados OCR, jobs)
│   └── auditoria.py         # Helpers de auditoría
│
├── templates/
│   └── index.html           # SPA principal (Jinja2)
│
├── static/
│   ├── app.js               # Lógica frontend (vanilla JS)
│   └── style.css            # Estilos
│
└── data/
    ├── mnist_model.pt       # Pesos CNN preentrenada (PyTorch)
    ├── actas/               # PDFs descargados (NO en git)
    ├── ocr/                 # Salidas OCR cacheadas (NO en git)
    └── reports/             # Reportes generados (NO en git)
```

---

## Clasificación forense (Tipos A-E)

El módulo `forensics.py` clasifica cada acta en una de cinco categorías peritas:

| Tipo | Descripción |
|---|---|
| **A** | Compatible con escán físico auténtico de acta en papel |
| **B** | Escán físico con señales menores de postprocesado digital |
| **C** | Origen mixto o indeterminado; requiere revisión adicional |
| **D** | Compatible con PDF generado digitalmente (no escaneado) |
| **E** | Señales significativas de manipulación digital posterior al escáner |

> ⚠️ **Nota pericial:** El análisis forense utiliza lenguaje de probabilidad ("compatible con", "consistente con", "indica señales de"). Ninguna clasificación constituye prueba legal de autenticidad o fraude.

---

## Metodología OCR para dígitos manuscritos

El OCR local divide la columna de votos de cada acta en sub-celdas por partido. Cada fila se divide en tres posiciones (centenas D0, decenas D1, unidades D2). La CNN MNIST clasifica cada sub-celda individualmente.

El sistema incluye filtros para dígitos guía impresos en los formularios ONPE:
- **Template de columna completa**: si el mismo dígito aparece con confianza ≥85% en ≥70% de las filas de una posición, se descarta como dígito decorativo impreso.
- **Template triple/doble**: patrones repetitivos en múltiples posiciones simultáneas.
- **Sanity check**: si ≥50% de los partidos tienen exactamente el mismo valor combinado, se descarta como artefacto.

---

## Privacidad y datos

- Los PDFs de las actas **no se incluyen** en el repositorio.
- La base de datos SQLite local **no se incluye** en el repositorio.
- Toda la información procesada proviene de la API **pública** de ONPE: [resultadoelectoral.onpe.gob.pe](https://resultadoelectoral.onpe.gob.pe)
- Las credenciales de Google Cloud **nunca deben subirse al repositorio**.

---

## Contribuir

1. Fork del repositorio
2. Crear rama: `git checkout -b feature/mi-mejora`
3. Commit: `git commit -m "feat: descripción"`
4. Push: `git push origin feature/mi-mejora`
5. Abrir Pull Request

---

## Licencia

MIT — Ver [LICENSE](LICENSE) para más detalles.

---

## Aviso legal

Esta herramienta es de uso exclusivamente ciudadano y educativo. No está afiliada ni patrocinada por la ONPE, el JNE ni ninguna organización política. Los resultados obtenidos no constituyen prueba legal. Para denuncias formales sobre actas, utilizar los canales oficiales del [Jurado Nacional de Elecciones (JNE)](https://www.jne.gob.pe).
