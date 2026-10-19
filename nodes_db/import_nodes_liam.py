#!/usr/bin/env python3
"""
Import Meshtastic nodes from JSON file to nodes_db.pkl
Extracts node_id, long_name, and short_name from nodes.json and adds them to the nodes database
used by the MeshTextualMonitor
"""

import json
import pickle
import os
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Same file the monitor loads (repo root, regardless of cwd)
DEFAULT_DB = os.path.join(os.path.dirname(SCRIPT_DIR), "nodes_db.pkl")
DEFAULT_INPUT = os.path.join(SCRIPT_DIR, "nodes.json")


def format_node_id(node_id):
    """
    Format node_id as hexadecimal with '!' prefix consistent with MeshTextualMonitor
    """
    if isinstance(node_id, int):
        return f"!{node_id & 0xFFFFFFFF:08x}"
    elif isinstance(node_id, str):
        if node_id.startswith("!"):
            return node_id.lower()
        try:
            return f"!{int(node_id) & 0xFFFFFFFF:08x}"
        except ValueError:
            return node_id
    return str(node_id)


def load_nodes_db(file_path=DEFAULT_DB):
    """
    Load the existing nodes database pickle file if it exists
    """
    if os.path.exists(file_path):
        try:
            with open(file_path, "rb") as file:
                nodes_db = pickle.load(file)
                print(f"Loaded existing nodes database with {len(nodes_db)} entries")
                return nodes_db
        except Exception as e:
            print(f"Error loading nodes_db from file: {e}")
            print("Creating new nodes database")

    # Return empty dict if file doesn't exist or error occurs
    return {}


def save_nodes_db(nodes_db, file_path=DEFAULT_DB):
    """
    Save the nodes database to pickle file
    """
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(file_path)), suffix=".tmp")
        with os.fdopen(fd, "wb") as file:
            pickle.dump(nodes_db, file)
        os.replace(tmp_path, file_path)
        print(f"Saved nodes database with {len(nodes_db)} entries")
        return True
    except Exception as e:
        print(f"Error saving nodes database: {e}")
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return False


def import_nodes_from_json(json_file, nodes_db=None, use_node_id_hex=True):
    """
    Import nodes from JSON file to nodes database

    Args:
        json_file: Path to the JSON file with nodes data
        nodes_db: Existing nodes database dict or None to create new
        use_node_id_hex: If True, use node_id_hex field if available

    Returns:
        Updated nodes database dict
    """
    if nodes_db is None:
        nodes_db = {}

    # Count stats
    new_nodes = 0
    updated_nodes = 0
    skipped_nodes = 0

    try:
        # Read and parse JSON file
        with open(json_file, "r", encoding="utf-8") as file:
            data = json.load(file)

        # Check if it's a JSON object with a 'nodes' array
        if isinstance(data, dict) and "nodes" in data:
            nodes = data["nodes"]
        elif isinstance(data, list):
            # Direct array of nodes
            nodes = data
        else:
            print(f"Unexpected JSON format in {json_file}")
            return nodes_db

        print(f"Found {len(nodes)} nodes in the JSON file")

        # Process each node
        for node in nodes:
            try:
                # Extract node ID - prefer node_id_hex if available and requested
                if use_node_id_hex and "node_id_hex" in node and node["node_id_hex"]:
                    node_id = node["node_id_hex"]
                elif "node_id" in node and node["node_id"]:
                    node_id = format_node_id(node["node_id"])
                else:
                    print(f"Skipping node with missing node_id: {node.get('id', 'unknown')}")
                    skipped_nodes += 1
                    continue

                # Extract node name - prefer long_name, fall back to short_name
                if "long_name" in node and node["long_name"]:
                    node_name = node["long_name"]
                elif "short_name" in node and node["short_name"]:
                    node_name = node["short_name"]
                else:
                    # If no name available, use hardware model if available, otherwise node_id
                    if "hardware_model_name" in node and node["hardware_model_name"]:
                        node_name = f"{node['hardware_model_name']}"
                    else:
                        node_name = node_id

                # Add additional info if available
                if "short_name" in node and node["short_name"] and node["short_name"] != node_name:
                    node_name = f"{node_name} ({node['short_name']})"

                # Check if node is already in database
                if node_id in nodes_db:
                    if nodes_db[node_id] != node_name:
                        print(f"Updating node {node_id}: {nodes_db[node_id]} -> {node_name}")
                        nodes_db[node_id] = node_name
                        updated_nodes += 1
                    else:
                        # Node exists with same name, skip
                        skipped_nodes += 1
                else:
                    # Add new node
                    print(f"Adding new node {node_id}: {node_name}")
                    nodes_db[node_id] = node_name
                    new_nodes += 1

            except Exception as e:
                print(f"Error processing node: {e}")
                skipped_nodes += 1

    except Exception as e:
        print(f"Error importing nodes from JSON: {e}")

    # Print summary
    print(f"\nImport summary:")
    print(f"  New nodes added: {new_nodes}")
    print(f"  Nodes updated: {updated_nodes}")
    print(f"  Nodes skipped: {skipped_nodes}")
    print(f"  Total nodes in database: {len(nodes_db)}")

    return nodes_db


def main():
    """
    Main function to run the script with argparse for command line arguments
    """
    import argparse

    # Set up argument parser
    parser = argparse.ArgumentParser(description="Import Meshtastic nodes from JSON file to nodes_db.pkl database")

    # Add arguments
    parser.add_argument("-i", "--input", default=DEFAULT_INPUT, help="Input JSON file with node data (default: %(default)s)")

    parser.add_argument("-o", "--output", default=DEFAULT_DB, help="Output pickle database file (default: %(default)s)")

    parser.add_argument("--skip-hex", action="store_false", dest="use_node_id_hex", help="Skip using node_id_hex field even if available")

    # Parse arguments
    args = parser.parse_args()

    print(f"Importing nodes from {args.input} to {args.output}")

    # Load existing nodes database
    nodes_db = load_nodes_db(args.output)

    # Import nodes from JSON
    nodes_db = import_nodes_from_json(args.input, nodes_db, args.use_node_id_hex)

    # Save updated nodes database
    save_nodes_db(nodes_db, args.output)


if __name__ == "__main__":
    main()
