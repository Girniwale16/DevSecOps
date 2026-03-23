import networkx as nx
from typing import Dict, List, Any

def build_schema_graph(tables: List[Dict[str, Any]], relations: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Constructs a serializable graph representation of the schema.
    Nodes = Tables
    Edges = Foreign Key Relationships
    """
    G = nx.DiGraph()
    
    # Add Nodes
    for table in tables:
        G.add_node(table['name'], 
                   id=table['id'],
                   row_count=table.get('row_count', 0))
        
    # Add Edges
    for rel in relations:
        G.add_edge(
            rel['from_table'], 
            rel['to_table'],
            id=rel['id'],
            from_column=rel['from_column'],
            to_column=rel['to_column'],
            cardinality=rel.get('cardinality', '1:N'),
            is_optional=rel.get('is_optional', True)
        )
        
    # Export to serializable format (adjacency list or node/link)
    # Using node-link format which is common for UI libraries
    return nx.node_link_data(G)

def get_topological_sort(tables: List[Dict[str, Any]], relations: List[Dict[str, Any]]) -> List[str]:
    """
    Returns table names in an order that respects foreign key dependencies.
    Parent tables will come before child tables.
    """
    G = nx.DiGraph()
    for table in tables:
        G.add_node(table['name'])
    for rel in relations:
        # Edge from parent to child to ensure parent is generated first
        G.add_edge(rel['to_table'], rel['from_table'])
        
    try:
        return list(nx.topological_sort(G))
    except nx.NetworkXUnfeasible:
        # Cyclic dependency handled by returning original order or raising
        return [t['name'] for t in tables]
