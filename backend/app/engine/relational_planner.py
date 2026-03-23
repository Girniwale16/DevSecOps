import networkx as nx
from typing import Dict, List, Any, Set

class RelationalPlanner:
    def __init__(self, tables: List[Dict[str, Any]], relations: List[Dict[str, Any]]):
        self.tables = {t['name']: t for t in tables}
        self.relations = relations
        self.dag = nx.DiGraph()
        self._build_dag()

    def _build_dag(self):
        """Builds a Directed Acyclic Graph where Edges point from Parent to Child."""
        for t_name in self.tables:
            self.dag.add_node(t_name)
        
        for rel in self.relations:
            # In our schema: from_table (child) -> to_table (parent)
            # For generation order: parent -> child
            parent = rel['to_table']
            child = rel['from_table']
            self.dag.add_edge(parent, child, **rel)

    def get_generation_order(self) -> List[str]:
        """Returns the topological sort of tables (Generation order)."""
        try:
            return list(nx.topological_sort(self.dag))
        except nx.NetworkXUnfeasible:
            # Fallback for cycles (simplified: return nodes in order)
            return list(self.dag.nodes())

    def get_execution_tiers(self) -> List[List[str]]:
        """Identifies parallelizable paths by grouping tables into tiers."""
        tiers = []
        # Copy the graph to destructiveley peel nodes with 0 in-degree
        remaining_graph = self.dag.copy()
        
        while remaining_graph.nodes():
            tier = [node for node, degree in remaining_graph.in_degree() if degree == 0]
            if not tier: # Potential cycle detected
                tier = list(remaining_graph.nodes())
                tiers.append(tier)
                break
            tiers.append(tier)
            remaining_graph.remove_nodes_from(tier)
            
        return tiers

    def calculate_propagation(self, base_rows: int = 100) -> Dict[str, int]:
        """
        Calculates row counts for each table based on propagation rules.
        Default: 
           - Root tables (no parents) = base_rows
           - 1:N links = parent_rows * 5 (typical distribution)
           - 1:1 links = parent_rows
        """
        planned_rows = {}
        for tier in self.get_execution_tiers():
            for table_name in tier:
                if self.dag.in_degree(table_name) == 0:
                    planned_rows[table_name] = base_rows
                else:
                    # Calculate rows based on parent(s)
                    parent_counts = []
                    for parent in self.dag.predecessors(table_name):
                        # Get relation attributes between parent and this table
                        edge_data = self.dag.get_edge_data(parent, table_name)
                        cardinality = edge_data.get('cardinality', '1:N')
                        
                        p_rows = planned_rows.get(parent, base_rows)
                        if cardinality == '1:1':
                            parent_counts.append(p_rows)
                        else:
                            # 1:N multiplier (default 5 for now)
                            parent_counts.append(p_rows * 5)
                    
                    # If multiple parents, take the max to ensure coverage or min for sparsity?
                    # Most relational mocks use mean or max. Let's use max for "full" datasets.
                    planned_rows[table_name] = max(parent_counts) if parent_counts else base_rows
                    
        return planned_rows

    def get_plan(self, base_rows: int = 100) -> Dict[str, Any]:
        """Returns a complete relational generation plan."""
        return {
            "order": self.get_generation_order(),
            "tiers": self.get_execution_tiers(),
            "row_counts": self.calculate_propagation(base_rows),
            "dag_edges": list(self.dag.edges(data=True))
        }
