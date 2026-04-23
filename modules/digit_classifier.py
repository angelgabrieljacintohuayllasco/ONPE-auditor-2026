"""
Clasificador de dígitos manuscritos basado en MNIST.
Usa una CNN pequeña entrenada en MNIST para reconocer dígitos 0-9
en sub-celdas de las actas electorales.

Mucho más preciso que Tesseract para dígitos manuscritos individuales.
"""
import os
import logging
import threading
import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

# ─── PyTorch ───
try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TORCH_OK = True
except ImportError:
    TORCH_OK = False
    logger.warning("PyTorch no disponible — clasificador MNIST deshabilitado")

MODEL_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'mnist_model.pt'))

_model_lock = threading.Lock()   # evita que varios hilos entrenen simultáneamente


class MNISTNet(nn.Module):
    """CNN simple para clasificación de dígitos MNIST (28x28 → 10 clases)."""
    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 32, 3, padding=1)
        self.conv2 = nn.Conv2d(32, 64, 3, padding=1)
        self.pool = nn.MaxPool2d(2, 2)
        self.dropout1 = nn.Dropout2d(0.25)
        self.dropout2 = nn.Dropout(0.5)
        self.fc1 = nn.Linear(64 * 7 * 7, 128)
        self.fc2 = nn.Linear(128, 10)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))   # 28→14
        x = self.pool(F.relu(self.conv2(x)))   # 14→7
        x = self.dropout1(x)
        x = x.view(-1, 64 * 7 * 7)
        x = F.relu(self.fc1(x))
        x = self.dropout2(x)
        x = self.fc2(x)
        return x


def train_mnist_model(epochs: int = 5, save_path: str = None) -> None:
    """Entrena el modelo MNIST y lo guarda en disco."""
    if not TORCH_OK:
        raise RuntimeError("PyTorch no disponible")

    from torchvision import datasets, transforms
    from torch.utils.data import DataLoader

    save_path = save_path or MODEL_PATH
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,))
    ])

    logger.info("Descargando MNIST...")
    data_dir = os.path.join(os.path.dirname(save_path), 'mnist_data')
    train_dataset = datasets.MNIST(data_dir, train=True, download=True, transform=transform)
    train_loader = DataLoader(train_dataset, batch_size=64, shuffle=True)

    model = MNISTNet()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.CrossEntropyLoss()

    model.train()
    for epoch in range(epochs):
        total_loss = 0
        correct = 0
        total = 0
        for batch_idx, (data, target) in enumerate(train_loader):
            optimizer.zero_grad()
            output = model(data)
            loss = criterion(output, target)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            pred = output.argmax(dim=1)
            correct += pred.eq(target).sum().item()
            total += target.size(0)

        acc = 100.0 * correct / total
        logger.info(f"  Epoch {epoch+1}/{epochs}: loss={total_loss/len(train_loader):.4f}, acc={acc:.1f}%")

    # Evaluar en test set
    test_dataset = datasets.MNIST(data_dir, train=False, download=True, transform=transform)
    test_loader = DataLoader(test_dataset, batch_size=1000)
    model.eval()
    correct = 0
    total = 0
    with torch.no_grad():
        for data, target in test_loader:
            output = model(data)
            pred = output.argmax(dim=1)
            correct += pred.eq(target).sum().item()
            total += target.size(0)
    acc = 100.0 * correct / total
    logger.info(f"  Test accuracy: {acc:.2f}%")

    torch.save(model.state_dict(), save_path)
    logger.info(f"  Modelo guardado en {save_path}")


_model_cache = None   # cache del modelo ya entrenado (cargado una vez por proceso)


def _get_model():
    """Carga el modelo entrenado (con cache). Thread-safe."""
    global _model_cache
    if _model_cache is not None:
        return _model_cache

    if not TORCH_OK:
        return None

    with _model_lock:
        # Doble-check dentro del lock: otro hilo ya puede haber cargado el modelo
        if _model_cache is not None:
            return _model_cache

        if not os.path.exists(MODEL_PATH):
            logger.info("Modelo MNIST no encontrado, entrenando...")
            try:
                train_mnist_model()
            except Exception as e:
                logger.error(f"Error entrenando MNIST: {e}")
                return None

        if not os.path.exists(MODEL_PATH):
            logger.error("El modelo MNIST no se pudo generar.")
            return None

        model = MNISTNet()
        model.load_state_dict(torch.load(MODEL_PATH, map_location='cpu', weights_only=True))
        model.eval()
        _model_cache = model
        logger.info("Modelo MNIST cargado")
        return model


def preprocess_cell_for_mnist(cell_img: Image.Image) -> np.ndarray:
    """
    Preprocesa una sub-celda de dígito para que sea compatible con MNIST.
    MNIST espera: 28x28, fondo negro (0), dígito blanco (1), centrado.

    Usa umbral fijo de intensidad (no Otsu) para evitar capturar el patrón
    de seguridad ondulado del formulario. La tinta manuscrita tiene min < 130,
    mientras que el patrón de seguridad tiene min > 150 típicamente.

    Args:
        cell_img: Imagen PIL de la sub-celda (cualquier tamaño, color o gris)

    Returns:
        Array numpy (28, 28) normalizado como MNIST, o None si celda vacía
    """
    # Convertir a escala de grises
    gray = cell_img.convert("L")
    arr = np.array(gray, dtype=np.float32)

    # Pre-filtro rápido: si el píxel más oscuro es > 145, no hay tinta
    if arr.min() > 145:
        return None

    # Umbral fijo en 140: solo captura tinta real (min ~91-120),
    # ignora el patrón de seguridad ondulado (>150)
    INK_THRESHOLD = 140
    binary = (arr < INK_THRESHOLD).astype(np.float32)

    # Detectar si hay contenido significativo (>=2% de píxeles oscuros)
    dark_pct = binary.mean()
    if dark_pct < 0.02:
        return None  # Celda vacía o solo ruido del patrón

    # ── Fallback a umbral más alto para trazos manuscritos suaves ──────────
    # Si la tinta capturada es muy escasa, el dígito puede haberse escrito con
    # menor presión (ej. curva inferior del "3" en píxeles 140-175).
    # Reintentamos con umbral 175 solo si gana sustancialmente más tinta
    # pero sin exceder 35% (para no capturar ruido de fondo o patrón impreso).
    # El filtrado CC posterior elimina fragmentos pequeños del patrón de seguridad.
    if dark_pct < 0.06:
        binary_hi = (arr < 175).astype(np.float32)
        dark_pct_hi = binary_hi.mean()
        if dark_pct_hi > dark_pct * 2.0 and dark_pct_hi < 0.35:
            binary = binary_hi
            dark_pct = dark_pct_hi

    # ── Filtrado de componentes conectados ──
    # El patrón de seguridad ondulado genera muchos fragmentos pequeños
    # dispersos por toda la celda. El dígito real es el componente más grande.
    # Mantener solo componentes significativos (>5% del total de tinta).
    from scipy import ndimage as _ndi
    labeled, num_features = _ndi.label(binary)
    if num_features > 1:
        sizes = _ndi.sum(binary, labeled, range(1, num_features + 1))
        total_dark = binary.sum()
        # Mantener componentes con >5% de la tinta total
        keep_mask = np.zeros_like(binary)
        for comp_id, sz in enumerate(sizes, 1):
            if sz / total_dark >= 0.05:
                keep_mask[labeled == comp_id] = 1.0
        # Solo usar filtrado si el componente principal no cubre toda la celda
        if keep_mask.sum() < total_dark:
            binary = keep_mask
            dark_pct = binary.mean()
            if dark_pct < 0.02:
                return None

    # Encontrar bounding box del dígito
    rows = np.any(binary > 0.5, axis=1)
    cols = np.any(binary > 0.5, axis=0)
    if not rows.any() or not cols.any():
        return None

    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]

    # Recortar al bounding box
    digit = binary[rmin:rmax+1, cmin:cmax+1]

    # Ajustar a cuadrado manteniendo aspect ratio, luego resize a 20x20
    dh, dw = digit.shape
    if dh == 0 or dw == 0:
        return None

    # Hacer cuadrado con padding
    max_dim = max(dh, dw)
    square = np.zeros((max_dim, max_dim), dtype=np.float32)
    pad_y = (max_dim - dh) // 2
    pad_x = (max_dim - dw) // 2
    square[pad_y:pad_y+dh, pad_x:pad_x+dw] = digit

    # Resize a 20x20 (MNIST tiene dígitos en 20x20 centrados en 28x28)
    from PIL import Image as PILImage
    sq_img = PILImage.fromarray((square * 255).astype(np.uint8), mode='L')
    sq_img = sq_img.resize((20, 20), PILImage.LANCZOS)

    # Centrar en 28x28 con borde de 4px
    mnist = np.zeros((28, 28), dtype=np.float32)
    arr20 = np.array(sq_img, dtype=np.float32) / 255.0
    mnist[4:24, 4:24] = arr20

    return mnist


def classify_digit(cell_img: Image.Image, confidence_threshold: float = 0.40) -> tuple:
    """
    Clasifica un dígito manuscrito en una sub-celda.

    Args:
        cell_img: Imagen PIL de la sub-celda
        confidence_threshold: Confianza mínima para aceptar resultado

    Returns:
        (digit: int, confidence: float) o (None, 0.0) si vacío o baja confianza
    """
    model = _get_model()
    if model is None:
        return None, 0.0

    mnist_arr = preprocess_cell_for_mnist(cell_img)
    if mnist_arr is None:
        return None, 0.0  # Celda vacía

    # Preparar tensor: (1, 1, 28, 28) normalizado como MNIST
    tensor = torch.tensor(mnist_arr, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
    # Normalización MNIST
    tensor = (tensor - 0.1307) / 0.3081

    with torch.no_grad():
        output = model(tensor)
        probs = F.softmax(output, dim=1)
        confidence, predicted = probs.max(1)

    digit = predicted.item()
    conf = confidence.item()

    # Correcciones geométricas para confusiones comunes.
    # Se aplica CC filtering para obtener geometría limpia del dígito.
    if digit in (2, 4, 7):
        gray = cell_img.convert("L")
        arr = np.array(gray, dtype=np.float32)
        binary = (arr < 140).astype(np.float32)
        # Mismo fallback de umbral que en preprocess_cell_for_mnist:
        # si la tinta es muy escasa (trazo suave), subir umbral a 175.
        if binary.mean() < 0.06:
            binary_hi = (arr < 175).astype(np.float32)
            if binary_hi.mean() > binary.mean() * 2.0 and binary_hi.mean() < 0.35:
                binary = binary_hi
        # CC filtering (mismo que en preprocess)
        from scipy import ndimage as _ndi
        _labeled, _nf = _ndi.label(binary)
        if _nf > 1:
            _sizes = _ndi.sum(binary, _labeled, range(1, _nf + 1))
            _total = binary.sum()
            _keep = np.zeros_like(binary)
            for _cid, _sz in enumerate(_sizes, 1):
                if _sz / _total >= 0.05:
                    _keep[_labeled == _cid] = 1.0
            if _keep.sum() < _total:
                binary = _keep

        rows = np.any(binary > 0.5, axis=1)
        cols = np.any(binary > 0.5, axis=0)
        if rows.any() and cols.any():
            rmin, rmax = np.where(rows)[0][[0, -1]]
            cmin, cmax = np.where(cols)[0][[0, -1]]
            bbox_h = rmax - rmin + 1
            bbox_w = cmax - cmin + 1
            aspect = bbox_w / bbox_h if bbox_h > 0 else 1.0

            if digit == 7:
                if aspect < 0.65:
                    digit = 1
                elif aspect < 0.85:
                    top_h = max(1, int(bbox_h * 0.30))
                    top_region = binary[rmin:rmin + top_h, cmin:cmax + 1]
                    top_cols = np.any(top_region > 0.5, axis=0)
                    if top_cols.any():
                        tc_min, tc_max = np.where(top_cols)[0][[0, -1]]
                        top_w = tc_max - tc_min + 1
                        top_ratio = top_w / bbox_w if bbox_w > 0 else 0
                        if top_ratio >= 0.95:
                            digit = 1
                    # 7→1: baja confianza + sin base ancha = no es "7"
                    if digit == 7 and conf < 0.55:
                        bot_h = max(1, int(bbox_h * 0.25))
                        bot_region = binary[rmax - bot_h + 1:rmax + 1, cmin:cmax + 1]
                        bot_cols = np.any(bot_region > 0.5, axis=0)
                        bot_w = bot_cols.sum() if bot_cols.any() else 0
                        bot_ratio = bot_w / bbox_w if bbox_w > 0 else 1.0
                        if bot_ratio < 0.35:
                            digit = 1
                elif aspect >= 0.85:
                    # 7→4: zona media vacía + baja confianza = probablemente "4"
                    mid_top = rmin + int(bbox_h * 0.35)
                    mid_bot = rmin + int(bbox_h * 0.65)
                    mid_region = binary[mid_top:mid_bot, cmin:cmax + 1]
                    mid_cols = np.any(mid_region > 0.5, axis=0)
                    mid_w = mid_cols.sum() if mid_cols.any() else 0
                    mid_ratio = mid_w / bbox_w if bbox_w > 0 else 1.0
                    if conf < 0.50 and mid_ratio < 0.30:
                        digit = 4
                    # 7→8/9: un "7" real no tiene base ancha. Si bot_ratio alto,
                    # contar agujeros encerrados para identificar dígito cerrado.
                    if digit == 7:
                        bot_h = max(1, int(bbox_h * 0.25))
                        bot_region = binary[rmax - bot_h + 1:rmax + 1, cmin:cmax + 1]
                        bot_cols = np.any(bot_region > 0.5, axis=0)
                        bot_w = bot_cols.sum() if bot_cols.any() else 0
                        bot_ratio = bot_w / bbox_w if bbox_w > 0 else 0
                        if bot_ratio > 0.30:
                            bbox_bin = binary[rmin:rmax + 1, cmin:cmax + 1]
                            # Cierre morfológico 2x2 para cerrar gaps en trazos manuscritos
                            closed = _ndi.binary_closing(bbox_bin, structure=np.ones((2, 2)))
                            inv = 1.0 - closed.astype(np.float32)
                            padded = np.pad(inv, 1, constant_values=1.0)
                            h_labeled, h_count = _ndi.label(padded)
                            n_holes = 0
                            for lbl in range(1, h_count + 1):
                                cm_h = (h_labeled == lbl)
                                if not (cm_h[0,:].any() or cm_h[-1,:].any() or
                                        cm_h[:,0].any() or cm_h[:,-1].any()):
                                    n_holes += 1
                            if n_holes >= 2:
                                digit = 8
                            elif n_holes >= 1:
                                digit = 8

                # ── Corrección final 7→3 ──────────────────────────────────────
                # Un "3" manuscrito puede clasificarse como "7" por MNIST porque
                # el trazo superior plano del 3 se parece al trazo del 7.
                # Diferencia geométrica clave: la ZONA MEDIA del "3" (35-65% de
                # altura) es MUY ANCHA porque ambas curvas crean anchura en esa
                # zona. El "7" auténtico solo tiene el trazo diagonal pasando por
                # el centro → anchura estrecha (<70% del bbox).
                if digit == 7:
                    p3 = probs[0, 3].item()
                    if p3 > 0.10:
                        mid_top_7 = rmin + int(bbox_h * 0.25)
                        mid_bot_7 = rmin + int(bbox_h * 0.75)
                        mid_reg_7 = binary[mid_top_7:mid_bot_7, cmin:cmax + 1]
                        mid_cols_7 = np.any(mid_reg_7 > 0.5, axis=0)
                        mid_w_7 = mid_cols_7.sum() if mid_cols_7.any() else 0
                        mid_width_ratio = mid_w_7 / bbox_w if bbox_w > 0 else 0
                        # Verificar tinta en parte inferior (no solo arco superior)
                        bot_c_h = max(1, int(bbox_h * 0.40))
                        bot_c = binary[rmax - bot_c_h + 1:rmax + 1, cmin:cmax + 1]
                        bot_dens = float(bot_c.sum()) / float(max(1, bot_c_h * bbox_w))
                        # 3: zona media ancha (>70% bbox) + tinta inferior presente
                        # 7: zona media estrecha (solo diagonal) → ratio <70%
                        # Salvaguarda: si P(4) >= 20%, el dígito es más probablemente
                        # un "4" (crossbar ancho) que un "3" → no corregir.
                        if (mid_width_ratio > 0.70 and bot_dens > 0.05
                                and probs[0, 4].item() < 0.20):
                            digit = 3

            elif digit == 2:
                bot_h = max(1, int(bbox_h * 0.25))
                bot_region = binary[rmax - bot_h + 1:rmax + 1, cmin:cmax + 1]
                bot_cols = np.any(bot_region > 0.5, axis=0)
                if bot_cols.any():
                    bot_w = bot_cols.sum()
                    bot_ratio = bot_w / bbox_w if bbox_w > 0 else 1.0
                    if bot_ratio < 0.35:
                        digit = 1

            elif digit == 4:
                # Corrección 4→1: Un "4" real tiene crossbar horizontal en la
                # banda media (35-65% de altura) que cubre >85% del ancho.
                # Un "1" con serifa confundido como "4" no tiene crossbar:
                # mid_ratio < 0.80. Real "4": mid_ratio ~0.98.
                mid_top = rmin + int(bbox_h * 0.35)
                mid_bot = rmin + int(bbox_h * 0.65)
                mid_region = binary[mid_top:mid_bot, cmin:cmax + 1]
                mid_cols = np.any(mid_region > 0.5, axis=0)
                mid_w = mid_cols.sum() if mid_cols.any() else 0
                mid_ratio = mid_w / bbox_w if bbox_w > 0 else 1.0
                if mid_ratio < 0.80:
                    digit = 1
                # 4→9: baja confianza + P(9) alta + top ancho = lazo cerrado de "9"
                elif conf < 0.65 and probs[0, 9].item() > 0.35:
                    top_h = max(1, int(bbox_h * 0.30))
                    top_region = binary[rmin:rmin + top_h, cmin:cmax + 1]
                    top_cols = np.any(top_region > 0.5, axis=0)
                    if top_cols.any():
                        tc_min, tc_max = np.where(top_cols)[0][[0, -1]]
                        top_w = tc_max - tc_min + 1
                        top_ratio = top_w / bbox_w if bbox_w > 0 else 0
                        if top_ratio > 0.85:
                            digit = 9

    if conf < confidence_threshold:
        return None, conf

    return digit, conf


def classify_3digit_cell(cell_img: Image.Image, x_left: int, x_right: int,
                          y_top: int, y_bot: int, full_img: Image.Image,
                          debug_dir: str = None, row_idx: int = 0) -> tuple:
    """
    Clasifica las 3 sub-celdas de una fila de dígitos (centenas, decenas, unidades).

    Args:
        full_img: Imagen completa de alta resolución
        x_left, x_right: Bordes de la columna de dígitos
        y_top, y_bot: Bordes superior e inferior de la fila
        debug_dir: Directorio para guardar imágenes de debug
        row_idx: Índice de fila para naming de debug

    Returns:
        (value: int or None, details: str)
    """
    third = (x_right - x_left) // 3
    margin_x = 8
    margin_y = 8

    digits = []
    confs = []

    for si in range(3):
        sx1 = x_left + si * third + margin_x
        sx2 = x_left + (si + 1) * third - margin_x
        sy1 = y_top + margin_y
        sy2 = y_bot - margin_y

        sub_cell = full_img.crop((sx1, sy1, sx2, sy2))

        digit, conf = classify_digit(sub_cell)

        if debug_dir and (row_idx < 8 or row_idx >= 33):
            mnist_arr = preprocess_cell_for_mnist(sub_cell)
            if mnist_arr is not None:
                mnist_img = Image.fromarray((mnist_arr * 255).astype(np.uint8), mode='L')
                mnist_img = mnist_img.resize((84, 84), Image.NEAREST)
                mnist_img.save(os.path.join(debug_dir, f'mnist_r{row_idx+1:02d}_d{si}.png'))

        digits.append(digit)
        confs.append(conf)

    # Combinar: centenas-decenas-unidades
    c = str(digits[0]) if digits[0] is not None else ''
    d = str(digits[1]) if digits[1] is not None else ''
    u = str(digits[2]) if digits[2] is not None else ''

    combined = c + d + u
    details = f"[{c or '_'}({confs[0]:.2f})][{d or '_'}({confs[1]:.2f})][{u or '_'}({confs[2]:.2f})]"

    if not combined:
        return None, details

    val = int(combined)
    if val > 999:
        return None, details

    return val, details


def classify_all_rows(full_img: Image.Image, x_left: int, x_right: int,
                      party_rows: list, debug_dir: str = None) -> list:
    """
    Clasifica todas las filas de votos y aplica filtro de template auto-detectado.

    El formulario ONPE tiene dígitos impresos de seguridad (decorativos) en algunas
    sub-celdas (especialmente D2/unidades). Estos se detectan automáticamente buscando
    un dígito que aparece repetidamente como detección aislada (solo en D2, sin D0/D1).

    Args:
        full_img: Imagen PIL completa a 500 DPI
        x_left, x_right: Bordes de la columna de votos
        party_rows: Lista de (y_top, y_bot) para cada fila
        debug_dir: Directorio de debug (opcional)

    Returns:
        Lista de (value, details) para cada fila
    """
    from collections import Counter

    third = (x_right - x_left) // 3
    margin_x = 8
    margin_y = 8
    num_rows = min(38, len(party_rows))

    # ── Paso 1: clasificar todas las sub-celdas ──
    raw = {}  # (row_idx, si) -> (digit, conf)
    cell_mins = {}  # (row_idx, si) -> min pixel value
    for row_idx in range(num_rows):
        y_top, y_bot = party_rows[row_idx]
        for si in range(3):
            sx1 = x_left + si * third + margin_x
            sx2 = x_left + (si + 1) * third - margin_x
            sy1 = y_top + margin_y
            sy2 = y_bot - margin_y
            sub_cell = full_img.crop((sx1, sy1, sx2, sy2))
            # Guardar min pixel para distinguir tinta manuscrita vs impresa
            gray_arr = np.array(sub_cell.convert("L"), dtype=np.float32)
            cell_mins[(row_idx, si)] = gray_arr.min()
            digit, conf = classify_digit(sub_cell)
            raw[(row_idx, si)] = (digit, conf)

            if debug_dir:
                mnist_arr = preprocess_cell_for_mnist(sub_cell)
                if mnist_arr is not None:
                    mnist_img = Image.fromarray((mnist_arr * 255).astype(np.uint8), mode='L')
                    mnist_img = mnist_img.resize((84, 84), Image.NEAREST)
                    mnist_img.save(os.path.join(debug_dir, f'mnist_r{row_idx+1:02d}_d{si}.png'))

    # ── Paso 2: auto-detectar dígito template en D2 ──
    # Solo contar como template si la celda tiene tinta tenue (min > 145),
    # lo que indica dígito impreso/decorativo, NO manuscrito.
    # Tinta manuscrita real tiene min < 130 típicamente.
    TEMPLATE_MIN_THRESHOLD = 145
    # Para discriminar tinta manuscrita real vs dígitos guía impresos:
    # manuscrita: pixel_min < 120 (trazo muy oscuro); impresa: > 120
    INK_DARK = 120

    d2_only = Counter()
    for row_idx in range(num_rows):
        d0, _ = raw[(row_idx, 0)]
        d1, _ = raw[(row_idx, 1)]
        d2, _ = raw[(row_idx, 2)]
        if d0 is None and d1 is None and d2 is not None:
            # Solo contar como candidato a template si la tinta es tenue
            if cell_mins.get((row_idx, 2), 0) > TEMPLATE_MIN_THRESHOLD:
                d2_only[d2] += 1

    tpl_digit = None
    if d2_only:
        most_common_digit, count = d2_only.most_common(1)[0]
        if count >= 3:
            tpl_digit = most_common_digit
            logger.info(f"Template D2 detectado: dígito '{tpl_digit}' ({count} ocurrencias aisladas)")

    # ── Paso 2d: template de posición única (D0, D1 o D2 impreso en TODAS las filas) ──
    # Si el mismo dígito aparece con confianza alta (≥0.85) en ≥70% de TODAS las filas
    # para una posición específica, es un dígito guía/decorativo impreso en esa columna.
    # Ejemplo: e10 Presidencial tiene D0=1 (conf=1.00) en las 38 filas → columna centenas guía.
    # Esta señal es imposible de dar por votos reales (implica que ≥70% de partidos
    # tienen la misma cantidad de centenas), por lo que es safe descartarla.
    SINGLE_COL_THRESHOLD = 0.70   # fracción de filas
    SINGLE_COL_MIN_CONF  = 0.85   # confianza mínima

    tpl_col = [None, None, None]  # tpl_col[pos] = dígito template o None
    for pos in range(3):
        high_conf = Counter()
        for row_idx in range(num_rows):
            d, c = raw[(row_idx, pos)]
            if d is not None and c >= SINGLE_COL_MIN_CONF:
                high_conf[d] += 1
        if high_conf:
            mc_dig, mc_cnt = high_conf.most_common(1)[0]
            if mc_cnt >= num_rows * SINGLE_COL_THRESHOLD:
                tpl_col[pos] = mc_dig
                logger.info(
                    f"Template columna D{pos} detectado: dígito '{mc_dig}' "
                    f"({mc_cnt}/{num_rows} filas, conf>={SINGLE_COL_MIN_CONF})"
                )

    # ── Paso 2b: template de triple (D0+D1+D2) ──
    # Los formularios ONPE tienen dígitos guía impresos en las tres sub-celdas.
    # Cuando muchas filas "vacías" leen [n][n][n] con tinta tenue (>INK_DARK),
    # se trata del dígito impreso, no de votos reales.
    triple_templates = Counter()
    for row_idx in range(num_rows):
        d0, c0 = raw[(row_idx, 0)]
        d1, c1 = raw[(row_idx, 1)]
        d2, c2 = raw[(row_idx, 2)]
        if d0 is not None and d1 is not None and d2 is not None:
            m0 = cell_mins.get((row_idx, 0), 0)
            m1 = cell_mins.get((row_idx, 1), 0)
            m2 = cell_mins.get((row_idx, 2), 0)
            # Tinta tenue en las 3 celdas → probable dígito impreso
            if min(m0, m1, m2) > INK_DARK:
                triple_templates[(d0, d1, d2)] += 1

    tpl_triple = None
    if triple_templates:
        most_common_triple, tc = triple_templates.most_common(1)[0]
        if tc >= 4:
            tpl_triple = most_common_triple
            logger.info(f"Template triple detectado: {most_common_triple} ({tc} filas, tinta tenue)")

    # ── Paso 2c: template de doble (D0+D1 o D1+D2) ──
    double_d01 = Counter()
    double_d12 = Counter()
    for row_idx in range(num_rows):
        d0, _ = raw[(row_idx, 0)]
        d1, _ = raw[(row_idx, 1)]
        d2, _ = raw[(row_idx, 2)]
        m0 = cell_mins.get((row_idx, 0), 0)
        m1 = cell_mins.get((row_idx, 1), 0)
        m2 = cell_mins.get((row_idx, 2), 0)
        if d0 is not None and d1 is not None and d2 is None and min(m0, m1) > INK_DARK:
            double_d01[(d0, d1)] += 1
        if d0 is None and d1 is not None and d2 is not None and min(m1, m2) > INK_DARK:
            double_d12[(d1, d2)] += 1

    tpl_d01 = None
    tpl_d12 = None
    if double_d01:
        dc, cnt = double_d01.most_common(1)[0]
        if cnt >= 4:
            tpl_d01 = dc
            logger.info(f"Template D01 detectado: {dc} ({cnt} filas)")
    if double_d12:
        dc, cnt = double_d12.most_common(1)[0]
        if cnt >= 4:
            tpl_d12 = dc
            logger.info(f"Template D12 detectado: {dc} ({cnt} filas)")

    # ── Paso 3: aplicar filtros y combinar ──
    results = []
    for row_idx in range(num_rows):
        d0, c0 = raw[(row_idx, 0)]
        d1, c1 = raw[(row_idx, 1)]
        d2, c2 = raw[(row_idx, 2)]
        note = ''

        m0 = cell_mins.get((row_idx, 0), 0)
        m1 = cell_mins.get((row_idx, 1), 0)
        m2 = cell_mins.get((row_idx, 2), 0)

        # Regla A: D2-aislado + coincide con template → descartar
        if d0 is None and d1 is None and d2 is not None and tpl_digit is not None and d2 == tpl_digit:
            d2 = None
            note = 'tpl_D2'

        # Regla G: columna completa template (D0/D1/D2 impreso en todas las filas)
        # Aplicar con prioridad antes de otras reglas: si la posición es template,
        # ignorarla independientemente del contexto.
        if tpl_col[0] is not None and d0 == tpl_col[0]:
            d0 = None
            note = 'tpl_col0'
        if tpl_col[1] is not None and d1 == tpl_col[1]:
            d1 = None
            note = (note + '+tpl_col1') if note else 'tpl_col1'
        if tpl_col[2] is not None and d2 == tpl_col[2]:
            d2 = None
            note = (note + '+tpl_col2') if note else 'tpl_col2'

        # Regla B: D1-aislado (D0=None, D2=None) con tinta tenue → descartar
        # Si la tinta es oscura (min < 145), es manuscrito real, no descartar.
        if d0 is None and d2 is None and d1 is not None:
            if cell_mins.get((row_idx, 1), 0) > TEMPLATE_MIN_THRESHOLD:
                d1 = None
                note = 'iso_D1'

        # Regla C: D2-aislado + baja confianza + tinta tenue → descartar
        # Solo filtrar si conf muy baja Y la tinta no es manuscrita oscura
        if d0 is None and d1 is None and d2 is not None and c2 < 0.7:
            if cell_mins.get((row_idx, 2), 0) > TEMPLATE_MIN_THRESHOLD:
                d2 = None
                note = 'low_D2'

        # Regla D: Triple template (D0+D1+D2 todos iguales al patrón impreso)
        if tpl_triple is not None and (d0, d1, d2) == tpl_triple:
            if min(m0, m1, m2) > INK_DARK:
                d0 = d1 = d2 = None
                note = 'tpl_triple'

        # Regla E: Doble template D0+D1
        if tpl_d01 is not None and d0 is not None and d1 is not None and d2 is None:
            if (d0, d1) == tpl_d01 and min(m0, m1) > INK_DARK:
                d0 = d1 = None
                note = 'tpl_D01'

        # Regla F: Doble template D1+D2
        if tpl_d12 is not None and d0 is None and d1 is not None and d2 is not None:
            if (d1, d2) == tpl_d12 and min(m1, m2) > INK_DARK:
                d1 = d2 = None
                note = 'tpl_D12'

        # Combinar centenas-decenas-unidades
        cs = str(d0) if d0 is not None else ''
        ds = str(d1) if d1 is not None else ''
        us = str(d2) if d2 is not None else ''
        combined = cs + ds + us
        details = f"[{cs or '_'}({c0:.2f})][{ds or '_'}({c1:.2f})][{us or '_'}({c2:.2f})]"
        if note:
            details += f" ({note})"

        if not combined:
            results.append((None, details))
        else:
            val = int(combined)
            results.append((val if val <= 999 else None, details))

    # ── Paso 4: sanity-check ──
    # Si ≥50% de las filas con valor tienen el mismo valor (artifact de template
    # que pasó los filtros), descartar esas filas específicas.
    from collections import Counter as _Ctr
    detected_vals = [(i, v) for i, (v, _) in enumerate(results) if v is not None and v > 0]
    if len(detected_vals) >= 6:
        val_counts = _Ctr(v for _, v in detected_vals)
        mc_val, mc_cnt = val_counts.most_common(1)[0]
        if mc_cnt >= len(detected_vals) * 0.50:
            logger.warning(
                f"  [SANITY] valor {mc_val} aparece en {mc_cnt}/{len(detected_vals)} filas "
                f"→ probable template, descartando esas filas"
            )
            results = [
                (None, det + " (sanity_dup)") if v == mc_val else (v, det)
                for i, (v, det) in enumerate(results)
            ]

    return results
