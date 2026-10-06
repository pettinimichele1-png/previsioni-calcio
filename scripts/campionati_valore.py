"""
CAMPIONATI IN PIU' PER LE GIOCATE DI VALORE (dalla v19.6)
=========================================================
Campionati che il nostro modello non segue ma che Pinnacle e almeno un
bookmaker italiano quotano: servono solo a valore.py, che non usa il
modello. previsioni.py tiene calendario e quote di queste partite (oggi
e domani) in quote_giro.json, dai download che fa gia' per tutto il
mondo: non costano chiamate. Il modello, le previsioni e l'archivio non
li vedono.

Scelti il 6/10/2026 sulle quote di un fine settimana: solo campionati,
niente coppe, niente Russia; fuori Cina, Indonesia, Egitto, serie
inglesi sotto la National League, riserve, giovanili e femminili.
id dell'API -> nome
"""

CAMPIONATI_VALORE = {
    # Inghilterra
    41: "England - League One", 42: "England - League Two", 43: "England - National League",
    # resto d'Europa
    80: "Germany - 3. Liga", 180: "Scotland - Championship",
    145: "Belgium - Challenger Pro League", 219: "Austria - 2. Liga",
    120: "Denmark - 1. Division", 114: "Sweden - Superettan", 104: "Norway - 1. Division",
    107: "Poland - I Liga", 271: "Hungary - NB I", 332: "Slovakia - Super Liga",
    373: "Slovenia - 1. SNL", 164: "Iceland - Úrvalsdeild", 408: "Northern-Ireland - Premiership",
    # Americhe
    71: "Brazil - Serie A", 128: "Argentina - Liga Profesional Argentina",
    129: "Argentina - Primera Nacional", 268: "Uruguay - Primera División",
    253: "USA - Major League Soccer", 255: "USA - USL Championship",
    262: "Mexico - Liga MX", 263: "Mexico - Liga de Expansión MX",
    162: "Costa-Rica - Primera División", 252: "Paraguay - Division Profesional - Clausura",
    # Asia
    99: "Japan - J2 League", 293: "South-Korea - K League 2",
}
