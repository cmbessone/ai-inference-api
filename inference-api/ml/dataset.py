"""Corpus mínimo de sentimiento (es/en) para entrenar los modelos de ejemplo.

Es deliberadamente chico: el foco del challenge es *cómo* se sirve el modelo (carga única,
versionado, thread pool, batching), no su calidad. En producción el artefacto vendría de un
pipeline de entrenamiento y un model registry (MLflow, S3 + manifest firmado).
"""

from __future__ import annotations

POSITIVE = (
    "me encantó el producto, excelente calidad",
    "el servicio fue increíble y muy rápido",
    "estoy muy contento con la compra",
    "excelente atención, lo recomiendo",
    "funciona perfecto, superó mis expectativas",
    "qué buena experiencia, volvería a comprar",
    "la entrega llegó antes de lo esperado, genial",
    "muy buena relación precio calidad",
    "el equipo de soporte fue amable y resolvió todo",
    "una maravilla, estoy feliz",
    "great product, I love it",
    "excellent service and fast delivery",
    "amazing experience, highly recommended",
    "the support team was very helpful",
    "works perfectly, very happy with it",
    "best purchase I have made this year",
    "fantastic quality for the price",
    "really good, exceeded my expectations",
)

NEGATIVE = (
    "el producto llegó roto, pésimo",
    "muy mala atención, no lo recomiendo",
    "estoy decepcionado con la compra",
    "el servicio fue lento y horrible",
    "no funciona, quiero un reembolso",
    "una estafa, nunca más compro acá",
    "la calidad es malísima",
    "el soporte nunca respondió, terrible",
    "llegó tarde y incompleto, un desastre",
    "odio este producto, es inútil",
    "terrible product, it broke in a day",
    "awful customer service, very disappointed",
    "worst purchase ever, waste of money",
    "it does not work, I want a refund",
    "very slow delivery and bad quality",
    "horrible experience, never again",
    "the item arrived damaged",
    "poor quality, not worth the price",
)

NEUTRAL = (
    "el pedido llegó el martes",
    "el producto es de color azul",
    "compré dos unidades la semana pasada",
    "la caja mide treinta centímetros",
    "el envío se hizo por correo",
    "tiene un manual de instrucciones",
    "lo usé una vez",
    "el precio figura en la página",
    "el paquete pesa dos kilos",
    "la tienda abre a las nueve",
    "the order arrived on tuesday",
    "the product is blue",
    "I bought two units last week",
    "it comes with a manual",
    "the package weighs two kilos",
    "the store opens at nine",
    "I used it once",
    "shipping was by mail",
)


def load() -> tuple[list[str], list[str]]:
    texts = [*POSITIVE, *NEGATIVE, *NEUTRAL]
    labels = ["positive"] * len(POSITIVE) + ["negative"] * len(NEGATIVE)
    labels += ["neutral"] * len(NEUTRAL)
    return texts, labels
