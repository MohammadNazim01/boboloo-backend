from app.services.analytics_engine.signal_extractor import extract_signals


# simulate conversation messages
messages = [
    {"role": "user", "content": "hello i see a red apple"},
    {"role": "user", "content": "apple is on the table"},
    {"role": "user", "content": "cat is near apple"},
    {"role": "user", "content": "cat is a red color"}
]

# run extractor
signals = extract_signals(messages)

print("\n--- SIGNAL OUTPUT ---")
print("Total Words:", signals["total_words"])
print("Content Words:", signals["content_word_count"])
print("Unique Words:", signals["unique_words"])

print("\n--- CONTENT WORDS LIST ---")
print(signals["content_words_list"])

# simulate vocabulary memory
vocab_memory = set()

# today's words
today_words = set(signals["content_words_list"])

# new words
new_words = today_words - vocab_memory

# update memory
vocab_memory.update(new_words)

print("\n--- NEW WORDS ---")
print(new_words)

print("\n--- VOCABULARY MEMORY ---")
print(vocab_memory)

print("\nVQ COUNT:", len(vocab_memory))