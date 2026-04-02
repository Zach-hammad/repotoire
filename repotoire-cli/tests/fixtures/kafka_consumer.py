from kafka import KafkaConsumer
import sqlite3

consumer = KafkaConsumer('my-topic', bootstrap_servers='localhost:9092')

def process_messages():
    conn = sqlite3.connect('db.sqlite')
    for message in consumer:
        result = conn.execute("SELECT * FROM users WHERE id = ?", (message.value,))
        process(result)

class EventWorker:
    def process(self, messages):
        conn = sqlite3.connect('db.sqlite')
        for msg in messages:
            conn.execute("UPDATE status SET processed = 1 WHERE id = ?", (msg.id,))
