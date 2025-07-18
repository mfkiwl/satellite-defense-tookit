# Ruta: modules/telemetry_lstm_monitor.py

import argparse
import logging
import yaml
import os
import json
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import socket
import platform
import websocket
from keras.models import Sequential, load_model
from keras.layers import LSTM, Dense
from sklearn.preprocessing import MinMaxScaler
import joblib

def load_config(path):
    with open(path, 'r') as f:
        return yaml.safe_load(f)

def setup_logging(log_file):
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logging.getLogger().addHandler(logging.StreamHandler())

def simulate_telemetry_stream(n_points=150):
    normal = np.random.normal(0, 1, (n_points, 1))
    anomaly = np.random.normal(5, 0.5, (10, 1))
    data = np.vstack([normal, anomaly])
    return data.flatten()

def prepare_lstm_data(data, look_back):
    scaler = MinMaxScaler()
    data_scaled = scaler.fit_transform(data.reshape(-1, 1))
    X, y = [], []
    for i in range(len(data_scaled) - look_back):
        X.append(data_scaled[i:i+look_back])
        y.append(data_scaled[i+look_back])
    return np.array(X), np.array(y), scaler

def build_and_train_model(X, y):
    model = Sequential()
    model.add(LSTM(20, input_shape=(X.shape[1], 1)))
    model.add(Dense(1))
    model.compile(loss='mean_squared_error', optimizer='adam')
    model.fit(X, y, epochs=10, batch_size=1, verbose=0)
    return model

def detect_anomalies_lstm(data, look_back, threshold, model_path, scaler_path):
    if os.path.exists(model_path) and os.path.exists(scaler_path):
        model = load_model(model_path)
        scaler = joblib.load(scaler_path)
        logging.info("[✓] Loaded existing model and scaler")
    else:
        logging.warning("[!] No model/scaler found — training from scratch")
        X, y, scaler = prepare_lstm_data(data, look_back)
        model = build_and_train_model(X, y)
        model.save(model_path)
        joblib.dump(scaler, scaler_path)
        logging.info("[+] New model trained and saved")

    data_scaled = scaler.transform(data.reshape(-1, 1))
    X, y = [], []
    for i in range(len(data_scaled) - look_back):
        X.append(data_scaled[i:i+look_back])
        y.append(data_scaled[i+look_back])
    X, y = np.array(X), np.array(y)

    predictions = model.predict(X, verbose=0)
    mse = np.mean(np.square(y - predictions.reshape(-1)))
    errors = np.square(y - predictions.reshape(-1))
    anomalies = errors > threshold * mse
    z_scores = (errors - np.mean(errors)) / np.std(errors)
    padded_anomalies = np.concatenate((np.zeros(look_back), anomalies))
    padded_z = np.concatenate((np.zeros(look_back), z_scores))
    return padded_anomalies, padded_z

def plot_anomalies(data, anomalies, output_path):
    plt.figure(figsize=(10, 4))
    plt.plot(data, label='Telemetry')
    plt.plot(np.where(anomalies)[0], data[anomalies.astype(bool)], 'ro', label='Anomalies')
    plt.title('Telemetry Anomaly Detection')
    plt.legend()
    plt.savefig(output_path)
    plt.close()

def send_ws_alert(ws_url, alert):
    try:
        ws = websocket.create_connection(ws_url, timeout=3)
        ws.send(json.dumps(alert))
        ws.close()
    except Exception as e:
        logging.warning(f"[WebSocket] Alert failed: {e}")

def log_to_agent_inventory(alert):
    os.makedirs("recon", exist_ok=True)
    path = "recon/agent_inventory.json"
    agent_id = socket.gethostname()
    if os.path.exists(path):
        with open(path, "r") as f:
            agents = json.load(f)
    else:
        agents = {}
    if agent_id not in agents:
        agents[agent_id] = {
            "telemetry": [],
            "ip": socket.gethostbyname(socket.gethostname()),
            "os": platform.system(),
            "host": agent_id
        }
    agents[agent_id]["telemetry"].append(alert)
    with open(path, "w") as f:
        json.dump(agents, f, indent=2)

def generate_stix_event(alert):
    return {
        "type": "indicator",
        "spec_version": "2.1",
        "id": f"indicator--{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "created": datetime.utcnow().isoformat(),
        "name": "Telemetry Anomaly",
        "description": f"Anomaly at point {alert['point_id']} | value={alert['value']:.2f} | z={alert['z_score']:.2f}",
        "pattern": "[x-telemetry:anomaly_score > 3]",
        "pattern_type": "stix",
        "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "collection"}],
        "labels": ["anomaly", "telemetry", "ai"]
    }

def export_report(alerts):
    os.makedirs("reports", exist_ok=True)
    with open("reports/telemetry_anomalies_stix.json", "w") as f:
        stix_alerts = [generate_stix_event(alert) for alert in alerts]
        json.dump(stix_alerts, f, indent=4)

    with open("reports/telemetry_report.md", "w") as f:
        for alert in alerts:
            f.write(f"- **Time**: {alert['timestamp']} | **Point**: {alert['point_id']} | **Value**: {alert['value']:.2f} | **Z**: {alert['z_score']:.2f}\n")

def enrich_with_ttp(alert):
    alert["mapped_ttp"] = "T1046"
    alert["kill_chain"] = "collection"
    return alert

def main(args):
    config = load_config(args.config)
    setup_logging(args.log)
    logging.info("== Satellite Defense Toolkit: Telemetry LSTM Monitor ==")

    telemetry_data = simulate_telemetry_stream()

    threshold = config.get("threshold", 3.0)
    method = config.get("method", "lstm")
    look_back = config.get("look_back", 10)
    ws_url = config.get("ws_url", "ws://localhost:8765")
    model_path = config.get("model_path", "models/lstm_model.h5")
    scaler_path = config.get("scaler_path", "models/scaler.pkl")

    if method != "lstm":
        raise NotImplementedError("Only LSTM detection is supported in fallback version.")

    anomalies, z_scores = detect_anomalies_lstm(telemetry_data, look_back, threshold, model_path, scaler_path)

    os.makedirs("results", exist_ok=True)
    alert_log = []
    for i, (val, is_anom, z) in enumerate(zip(telemetry_data, anomalies, z_scores)):
        if is_anom:
            alert = {
                "timestamp": datetime.utcnow().isoformat(),
                "point_id": i,
                "value": float(val),
                "z_score": float(z),
                "alert": "ANOMALY_DETECTED"
            }
            alert = enrich_with_ttp(alert)
            alert_log.append(alert)
            log_to_agent_inventory(alert)
            send_ws_alert(ws_url, alert)

    with open("results/telemetry_anomalies.json", "w") as f:
        json.dump(alert_log, f, indent=4)

    plot_anomalies(telemetry_data, anomalies, "results/telemetry_anomaly_plot.png")
    export_report(alert_log)
    logging.info(f"[✓] {len(alert_log)} anomalies detected. Reports exported.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telemetry Anomaly Monitor (LSTM)")
    parser.add_argument("--config", default="config/config.yaml", help="YAML config path")
    parser.add_argument("--log", default="logs/telemetry_monitor.log", help="Log file path")
    args = parser.parse_args()
    main(args)
