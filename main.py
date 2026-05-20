import g4f

# Get a list of all available provider names
all_providers = g4f.Provider.__all__

print("--- AVAILABLE G4F PROVIDERS ---")
for provider in all_providers:
    # We only want providers that are likely free and don't need special setup
    if "g4f" not in provider.lower() and "replicate" not in provider.lower():
         print(provider)
print("--- END OF LIST ---")
