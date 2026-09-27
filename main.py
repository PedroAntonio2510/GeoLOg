import os
import random
import sqlite3
from datetime import datetime, timezone, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st
import folium

from pymongo import MongoClient, GEOSPHERE
from streamlit_folium import st_folium

# ============================================================
# CONFIGURAÇÕES
# ============================================================

SQLITE_DB = "logitech.db"

# Pode definir via variável de ambiente:
# MONGO_URI="mongodb://localhost:27017"
#
# Ou, no Streamlit Cloud, usar:
# st.secrets["MONGO_URI"]
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

MONGO_DATABASE = "geolog_db"
MONGO_COLLECTION = "telemetria"

VELOCIDADE_ALERTA = 80

# ============================================================
# DADOS INICIAIS
# ============================================================

MOTORISTAS = [
    (1, "Carlos Andrade", "123456789", "Ativo"),
    (2, "Mariana Silva", "987654321", "Ativo"),
    (3, "Roberto Souza", "456789123", "Em Descanso"),
]

VEICULOS = [
    (101, "ABC-1A23", "Volvo FH 540", 1),
    (102, "XYZ-9876", "Scania R450", 2),
    (103, "KGB-4567", "Mercedes Actros", 3),
]

TELEMETRIA_SEED = [
    {
        "veiculo_id": 101,
        "location": {
            "type": "Point",
            "coordinates": [-34.873, -7.115]
        },
        "temperatura": 4.2,
        "velocidade": 65,
        "timestamp": datetime(
            2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc
        ),
    },
    {
        "veiculo_id": 102,
        "location": {
            "type": "Point",
            "coordinates": [-34.832, -7.121]
        },
        "temperatura": -18.5,
        "velocidade": 85,
        "timestamp": datetime(
            2026, 9, 11, 10, 5, 0, tzinfo=timezone.utc
        ),
    },
    {
        "veiculo_id": 103,
        "location": {
            "type": "Point",
            "coordinates": [-34.950, -7.150]
        },
        "temperatura": 22.0,
        "velocidade": 0,
        "timestamp": datetime(
            2026, 9, 11, 9, 45, 0, tzinfo=timezone.utc
        ),
    },
]

# ============================================================
# ESTILO / CONFIGURAÇÃO STREAMLIT
# ============================================================

st.set_page_config(
    page_title="GeoLog - Telemetria Logística",
    page_icon="🚛",
    layout="wide",
)


# ============================================================
# SQLITE
# ============================================================

def get_sqlite_connection():
    """
    Abre conexão com o banco SQLite.
    """
    return sqlite3.connect(SQLITE_DB)


def init_sqlite():
    """
    Cria as tabelas relacionais e insere os dados iniciais.
    """

    conn = get_sqlite_connection()
    cursor = conn.cursor()

    # Tabela de motoristas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS motoristas (
            id INTEGER PRIMARY KEY,
            nome TEXT NOT NULL,
            cnh TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL
        )
    """)

    # Tabela de veículos
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS veiculos (
            id INTEGER PRIMARY KEY,
            placa TEXT NOT NULL UNIQUE,
            modelo TEXT NOT NULL,
            motorista_id INTEGER NOT NULL,
            FOREIGN KEY (motorista_id)
                REFERENCES motoristas(id)
        )
    """)

    # Seed dos motoristas
    cursor.executemany("""
        INSERT OR IGNORE INTO motoristas
        (id, nome, cnh, status)
        VALUES (?, ?, ?, ?)
    """, MOTORISTAS)

    # Seed dos veículos
    cursor.executemany("""
        INSERT OR IGNORE INTO veiculos
        (id, placa, modelo, motorista_id)
        VALUES (?, ?, ?, ?)
    """, VEICULOS)

    conn.commit()
    conn.close()


def get_veiculos():
    """
    Retorna os veículos com seus respectivos motoristas.
    """

    conn = get_sqlite_connection()

    query = """
        SELECT
            v.id AS veiculo_id,
            v.placa,
            v.modelo,
            m.id AS motorista_id,
            m.nome AS motorista,
            m.cnh,
            m.status
        FROM veiculos v
        INNER JOIN motoristas m
            ON v.motorista_id = m.id
        ORDER BY v.id
    """

    df = pd.read_sql_query(query, conn)

    conn.close()

    return df


def get_motoristas():
    """
    Retorna os motoristas.
    """

    conn = get_sqlite_connection()

    df = pd.read_sql_query("""
        SELECT
            id,
            nome,
            cnh,
            status
        FROM motoristas
        ORDER BY id
    """, conn)

    conn.close()

    return df


# ============================================================
# MONGODB
# ============================================================

@st.cache_resource
def get_mongo_collection():
    """
    Cria conexão com MongoDB e retorna a coleção de telemetria.
    """

    client = MongoClient(MONGO_URI)

    # Testa conexão
    client.admin.command("ping")

    db = client[MONGO_DATABASE]
    collection = db[MONGO_COLLECTION]

    # ========================================================
    # ÍNDICE GEOESPACIAL OBRIGATÓRIO
    # ========================================================

    collection.create_index(
        [("location", GEOSPHERE)],
        name="location_2dsphere"
    )

    return collection


def init_mongodb(collection):
    """
    Insere os dados de seed caso a coleção esteja vazia.
    """

    if collection.count_documents({}) == 0:
        collection.insert_many(TELEMETRIA_SEED)

        st.success(
            "Dados iniciais de telemetria inseridos no MongoDB."
        )


# ============================================================
# TELEMETRIA
# ============================================================

def get_all_telemetry(collection):
    """
    Busca todos os registros de telemetria.
    """

    documents = list(
        collection.find(
            {},
            {
                "_id": 0,
                "veiculo_id": 1,
                "location": 1,
                "temperatura": 1,
                "velocidade": 1,
                "timestamp": 1
            }
        ).sort("timestamp", 1)
    )

    return documents


def get_latest_telemetry(collection):
    """
    Obtém o último registro de telemetria de cada veículo.

    A agregação é feita no MongoDB.
    """

    pipeline = [
        {
            "$sort": {
                "timestamp": -1
            }
        },
        {
            "$group": {
                "_id": "$veiculo_id",
                "veiculo_id": {
                    "$first": "$veiculo_id"
                },
                "temperatura": {
                    "$first": "$temperatura"
                },
                "velocidade": {
                    "$first": "$velocidade"
                },
                "location": {
                    "$first": "$location"
                },
                "timestamp": {
                    "$first": "$timestamp"
                }
            }
        },
        {
            "$sort": {
                "veiculo_id": 1
            }
        }
    ]

    return list(collection.aggregate(pipeline))


# ============================================================
# BUSCA GEOESPACIAL
# ============================================================

def buscar_por_raio(collection, latitude, longitude, raio_km):
    """
    Busca veículos dentro de determinado raio utilizando
    o operador $near do MongoDB.

    O MongoDB trabalha com distância em metros.
    """

    raio_metros = raio_km * 1000

    ponto = {
        "type": "Point",
        "coordinates": [
            longitude,
            latitude
        ]
    }

    documentos = list(
        collection.find(
            {
                "location": {
                    "$near": {
                        "$geometry": ponto,
                        "$maxDistance": raio_metros
                    }
                }
            }
        )
    )

    return documentos


# ============================================================
# JOIN POLIGLOTA
# ============================================================

def criar_visao_unificada(df_veiculos, latest_telemetry):
    """
    Realiza o Join Poliglota em memória.

    SQLite:
        motorista
        placa
        modelo
        status

    MongoDB:
        temperatura
        velocidade
        localização
        timestamp
    """

    if not latest_telemetry:
        return pd.DataFrame()

    telemetry_rows = []

    for item in latest_telemetry:
        coordinates = item["location"]["coordinates"]

        longitude = coordinates[0]
        latitude = coordinates[1]

        telemetry_rows.append({
            "veiculo_id": item["veiculo_id"],
            "temperatura": item["temperatura"],
            "velocidade": item["velocidade"],
            "longitude": longitude,
            "latitude": latitude,
            "timestamp": item["timestamp"]
        })

    df_telemetry = pd.DataFrame(telemetry_rows)

    # JOIN realizado em memória utilizando Pandas
    df = pd.merge(
        df_veiculos,
        df_telemetry,
        on="veiculo_id",
        how="inner"
    )

    return df


# ============================================================
# SIMULADOR DE TELEMETRIA
# ============================================================

def simular_movimentacao(collection):
    """
    Cria novos registros de telemetria com pequenas
    variações aleatórias de latitude e longitude.
    """

    latest = get_latest_telemetry(collection)

    if not latest:
        return

    novos_documentos = []

    for item in latest:
        longitude = item["location"]["coordinates"][0]
        latitude = item["location"]["coordinates"][1]

        # Pequena variação geográfica
        nova_longitude = longitude + random.uniform(-0.005, 0.005)
        nova_latitude = latitude + random.uniform(-0.005, 0.005)

        # Pequena variação de temperatura
        nova_temperatura = item["temperatura"] + random.uniform(
            -1.0,
            1.0
        )

        # Pequena variação de velocidade
        nova_velocidade = max(
            0,
            item["velocidade"] + random.randint(-10, 10)
        )

        novos_documentos.append({
            "veiculo_id": item["veiculo_id"],
            "location": {
                "type": "Point",
                "coordinates": [
                    nova_longitude,
                    nova_latitude
                ]
            },
            "temperatura": round(nova_temperatura, 2),
            "velocidade": nova_velocidade,
            "timestamp": datetime.now(timezone.utc)
        })

    if novos_documentos:
        collection.insert_many(novos_documentos)


# ============================================================
# MAPA
# ============================================================

def criar_mapa(
        df_unificado,
        latitude_centro,
        longitude_centro,
        raio_km=None,
        resultados_raio=None
):
    """
    Cria mapa Folium com os veículos e área de busca.
    """

    mapa = folium.Map(
        location=[
            latitude_centro,
            longitude_centro
        ],
        zoom_start=12,
        control_scale=True
    )

    # --------------------------------------------------------
    # Ponto de referência
    # --------------------------------------------------------

    folium.Marker(
        location=[
            latitude_centro,
            longitude_centro
        ],
        popup="Ponto de Referência",
        tooltip="Ponto de Busca",
        icon=folium.Icon(
            color="red",
            icon="search"
        )
    ).add_to(mapa)

    # --------------------------------------------------------
    # Área do raio
    # --------------------------------------------------------

    if raio_km is not None:
        folium.Circle(
            location=[
                latitude_centro,
                longitude_centro
            ],
            radius=raio_km * 1000,
            popup=f"Raio: {raio_km:.1f} km",
            fill=True
        ).add_to(mapa)

    # --------------------------------------------------------
    # Veículos
    # --------------------------------------------------------

    if resultados_raio is not None:

        veiculos_para_mostrar = resultados_raio

    else:

        veiculos_para_mostrar = []

        if df_unificado is not None:

            for _, row in df_unificado.iterrows():
                veiculos_para_mostrar.append({
                    "veiculo_id": row["veiculo_id"],
                    "location": {
                        "coordinates": [
                            row["longitude"],
                            row["latitude"]
                        ]
                    },
                    "temperatura": row["temperatura"],
                    "velocidade": row["velocidade"]
                })

    for item in veiculos_para_mostrar:

        coords = item["location"]["coordinates"]

        longitude = coords[0]
        latitude = coords[1]

        velocidade = item.get("velocidade", 0)
        temperatura = item.get("temperatura", 0)
        veiculo_id = item.get("veiculo_id")

        if velocidade > VELOCIDADE_ALERTA:
            cor = "red"
        else:
            cor = "blue"

        popup = folium.Popup(
            f"""
            <b>Veículo:</b> {veiculo_id}<br>
            <b>Temperatura:</b> {temperatura:.2f} °C<br>
            <b>Velocidade:</b> {velocidade} km/h
            """,
            max_width=300
        )

        folium.Marker(
            location=[
                latitude,
                longitude
            ],
            popup=popup,
            tooltip=f"Veículo {veiculo_id}",
            icon=folium.Icon(
                color=cor,
                icon="truck",
                prefix="fa"
            )
        ).add_to(mapa)

    return mapa


# ============================================================
# DASHBOARD
# ============================================================

def exibir_kpis(df):
    """
    Exibe os principais indicadores.
    """

    if df.empty:
        st.warning(
            "Não existem dados de telemetria disponíveis."
        )

        return

    total_frotas_ativas = len(
        df[
            df["status"].str.lower() == "ativo"
            ]
    )

    media_temperatura = df["temperatura"].mean()

    alertas_velocidade = len(
        df[
            df["velocidade"] > VELOCIDADE_ALERTA
            ]
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            "🚛 Frotas Ativas",
            total_frotas_ativas
        )

    with col2:
        st.metric(
            "🌡️ Temperatura Média",
            f"{media_temperatura:.2f} °C"
        )

    with col3:
        st.metric(
            "⚠️ Alertas de Velocidade",
            alertas_velocidade
        )


# ============================================================
# GRÁFICO DE TEMPERATURA
# ============================================================

def grafico_temperatura(collection, df_veiculos):
    """
    Histórico de temperatura por veículo.
    """

    documentos = get_all_telemetry(collection)

    if not documentos:
        st.info(
            "Não existem dados históricos."
        )

        return

    rows = []

    for item in documentos:
        rows.append({
            "veiculo_id": item["veiculo_id"],
            "temperatura": item["temperatura"],
            "timestamp": item["timestamp"]
        })

    df = pd.DataFrame(rows)

    df["timestamp"] = pd.to_datetime(
        df["timestamp"]
    )

    # Associar placa ao veículo
    df = df.merge(
        df_veiculos[
            [
                "veiculo_id",
                "placa"
            ]
        ],
        on="veiculo_id",
        how="left"
    )

    fig = px.line(
        df,
        x="timestamp",
        y="temperatura",
        color="placa",
        markers=True,
        title="Histórico de Temperatura por Veículo",
        labels={
            "timestamp": "Data/Hora",
            "temperatura": "Temperatura (°C)",
            "placa": "Veículo"
        }
    )

    st.plotly_chart(
        fig,
        use_container_width=True
    )


# ============================================================
# GRÁFICO DE STATUS DOS MOTORISTAS
# ============================================================

def grafico_status_motoristas():
    """
    Distribuição do status dos motoristas.
    """

    df = get_motoristas()

    if df.empty:
        return

    status = (
        df
        .groupby("status")
        .size()
        .reset_index(name="quantidade")
    )

    fig = px.bar(
        status,
        x="status",
        y="quantidade",
        title="Distribuição do Status dos Motoristas",
        labels={
            "status": "Status",
            "quantidade": "Quantidade"
        },
        text_auto=True
    )

    st.plotly_chart(
        fig,
        use_container_width=True
    )


# ============================================================
# APLICAÇÃO PRINCIPAL
# ============================================================

def main():
    # --------------------------------------------------------
    # Inicialização dos bancos
    # --------------------------------------------------------

    init_sqlite()

    st.title("🚛 GeoLog")
    st.subheader(
        "Plataforma de Telemetria Logística e Persistência Poliglota"
    )

    st.markdown(
        """
        **SQLite + MongoDB + Streamlit + Folium + Plotly**

        Sistema de monitoramento de veículos utilizando
        persistência poliglota e consultas geoespaciais.
        """
    )

    # --------------------------------------------------------
    # Conexão MongoDB
    # --------------------------------------------------------

    try:

        collection = get_mongo_collection()

        init_mongodb(collection)

    except Exception as e:

        st.error(
            "Não foi possível conectar ao MongoDB."
        )

        st.code(str(e))

        st.info(
            "Verifique se o MongoDB está executando "
            "e se a variável MONGO_URI está correta."
        )

        st.stop()

    # --------------------------------------------------------
    # Dados
    # --------------------------------------------------------

    df_veiculos = get_veiculos()

    latest_telemetry = get_latest_telemetry(
        collection
    )

    df_unificado = criar_visao_unificada(
        df_veiculos,
        latest_telemetry
    )

    # --------------------------------------------------------
    # SIDEBAR
    # --------------------------------------------------------

    st.sidebar.header("⚙️ Controles")

    st.sidebar.markdown(
        "### Banco de Dados"
    )

    st.sidebar.success(
        "SQLite conectado"
    )

    st.sidebar.success(
        "MongoDB conectado"
    )

    st.sidebar.markdown("---")

    # --------------------------------------------------------
    # SIMULAÇÃO
    # --------------------------------------------------------

    st.sidebar.markdown(
        "### 🚀 Simulador"
    )

    if st.sidebar.button(
            "Simular Movimentação",
            use_container_width=True
    ):
        simular_movimentacao(
            collection
        )

        st.success(
            "Nova telemetria gerada com sucesso!"
        )

        st.rerun()

    # --------------------------------------------------------
    # NAVEGAÇÃO
    # --------------------------------------------------------

    pagina = st.sidebar.radio(
        "Módulo",
        [
            "📊 Dashboard",
            "🗺️ Busca Geoespacial",
            "🚛 Visão Unificada",
            "💾 Bancos de Dados"
        ]
    )

    # ========================================================
    # DASHBOARD
    # ========================================================

    if pagina == "📊 Dashboard":

        st.header(
            "📊 Dashboard Analítico"
        )

        exibir_kpis(
            df_unificado
        )

        st.markdown("---")

        col1, col2 = st.columns(2)

        with col1:

            st.subheader(
                "🌡️ Temperatura"
            )

            grafico_temperatura(
                collection,
                df_veiculos
            )

        with col2:

            st.subheader(
                "👨‍✈️ Status dos Motoristas"
            )

            grafico_status_motoristas()

        st.markdown("---")

        st.subheader(
            "🚨 Alertas de Velocidade"
        )

        if not df_unificado.empty:

            alertas = df_unificado[
                df_unificado["velocidade"]
                > VELOCIDADE_ALERTA
                ]

            if alertas.empty:

                st.success(
                    "Nenhum alerta de velocidade."
                )

            else:

                st.warning(
                    f"{len(alertas)} veículo(s) "
                    "acima do limite."
                )

                st.dataframe(
                    alertas[
                        [
                            "placa",
                            "motorista",
                            "velocidade",
                            "temperatura"
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True
                )

    # ========================================================
    # BUSCA GEOESPACIAL
    # ========================================================

    elif pagina == "🗺️ Busca Geoespacial":

        st.header(
            "🗺️ Busca Geoespacial"
        )

        st.write(
            """
            Utilize o índice **2dsphere** do MongoDB para
            localizar veículos dentro de um determinado raio.
            """
        )

        st.info(
            "O ponto padrão utiliza coordenadas próximas "
            "ao centro de João Pessoa/PB."
        )

        col1, col2 = st.columns(2)

        with col1:

            latitude = st.number_input(
                "Latitude",
                value=-7.115,
                format="%.6f"
            )

        with col2:

            longitude = st.number_input(
                "Longitude",
                value=-34.873,
                format="%.6f"
            )

        raio_km = st.slider(
            "Raio de busca (km)",
            min_value=1.0,
            max_value=50.0,
            value=10.0,
            step=1.0
        )

        if st.button(
                "🔎 Buscar Veículos",
                type="primary"
        ):
            resultados = buscar_por_raio(
                collection,
                latitude,
                longitude,
                raio_km
            )

            st.session_state[
                "resultados_geo"
            ] = resultados

        resultados = st.session_state.get(
            "resultados_geo",
            []
        )

        if resultados:

            st.success(
                f"{len(resultados)} veículo(s) "
                f"encontrado(s) em até {raio_km:.1f} km."
            )

            # ------------------------------------------------
            # Resultado textual
            # ------------------------------------------------

            tabela_geo = []

            for item in resultados:
                coords = item[
                    "location"
                ]["coordinates"]

                tabela_geo.append({
                    "Veículo": item["veiculo_id"],
                    "Latitude": coords[1],
                    "Longitude": coords[0],
                    "Temperatura": item["temperatura"],
                    "Velocidade": item["velocidade"],
                    "Data/Hora": item["timestamp"]
                })

            df_geo = pd.DataFrame(
                tabela_geo
            )

            st.dataframe(
                df_geo,
                use_container_width=True,
                hide_index=True
            )

            # ------------------------------------------------
            # Mapa
            # ------------------------------------------------

            mapa = criar_mapa(
                df_unificado,
                latitude,
                longitude,
                raio_km,
                resultados
            )

            st_folium(
                mapa,
                width=None,
                height=550
            )

        else:

            st.warning(
                "Nenhum veículo encontrado dentro "
                "do raio informado."
            )

            mapa = criar_mapa(
                df_unificado,
                latitude,
                longitude,
                raio_km,
                []
            )

            st_folium(
                mapa,
                width=None,
                height=550
            )

    # ========================================================
    # VISÃO UNIFICADA
    # ========================================================

    elif pagina == "🚛 Visão Unificada":

        st.header(
            "🚛 Visão Unificada"
        )

        st.write(
            """
            Esta tela demonstra o **Join Poliglota em Memória**.

            Os dados cadastrais são recuperados do SQLite,
            enquanto os dados de telemetria vêm do MongoDB.
            """
        )

        if df_unificado.empty:

            st.warning(
                "Nenhum dado disponível."
            )

        else:

            # Criar uma tabela exatamente no formato solicitado
            tabela = df_unificado[
                [
                    "motorista",
                    "placa",
                    "temperatura",
                    "velocidade",
                    "latitude",
                    "longitude",
                    "timestamp"
                ]
            ].copy()

            tabela.columns = [
                "Nome do Motorista",
                "Placa",
                "Última Temperatura",
                "Velocidade",
                "Latitude",
                "Longitude",
                "Última Atualização"
            ]

            st.dataframe(
                tabela,
                use_container_width=True,
                hide_index=True
            )

        st.markdown("---")

        st.subheader(
            "📋 Dados completos"
        )

        st.dataframe(
            df_unificado,
            use_container_width=True,
            hide_index=True
        )

    # ========================================================
    # BANCOS
    # ========================================================

    elif pagina == "💾 Bancos de Dados":

        st.header(
            "💾 Persistência Poliglota"
        )

        st.subheader(
            "SQLite — Dados Transacionais"
        )

        st.dataframe(
            df_veiculos,
            use_container_width=True,
            hide_index=True
        )

        st.markdown("---")

        st.subheader(
            "MongoDB — Telemetria"
        )

        documentos = get_all_telemetry(
            collection
        )

        mongo_rows = []

        for item in documentos:
            coords = item[
                "location"
            ]["coordinates"]

            mongo_rows.append({
                "veiculo_id": item["veiculo_id"],
                "longitude": coords[0],
                "latitude": coords[1],
                "temperatura": item["temperatura"],
                "velocidade": item["velocidade"],
                "timestamp": item["timestamp"]
            })

        df_mongo = pd.DataFrame(
            mongo_rows
        )

        st.dataframe(
            df_mongo,
            use_container_width=True,
            hide_index=True
        )

        st.markdown("---")

        st.subheader(
            "🌎 Índice Geoespacial"
        )

        st.code(
            """
collection.create_index(
    [("location", GEOSPHERE)],
    name="location_2dsphere"
)
            """,
            language="python"
        )

        st.success(
            "Índice 2dsphere criado durante "
            "a inicialização da aplicação."
        )


# ============================================================
# EXECUÇÃO
# ============================================================

if __name__ == "__main__":
    main()