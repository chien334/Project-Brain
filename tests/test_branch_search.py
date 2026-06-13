import pytest
import asyncio
from projectbrain.client import Memory
from projectbrain.core.db import db
from projectbrain.core.config import env

@pytest.mark.asyncio
async def test_branch_wildcard_search():
    # Lower min_score so synthetic embeddings pass in tests
    env.min_score = 0.1
    mem = Memory()
    
    # Define projects and branches
    proj_main = "test-project-unique-xyz:main"
    proj_feature = "test-project-unique-xyz:feature-x"
    proj_other = "other-project-unique:main"
    
    # Clean up first
    await mem.delete_all(user_id=proj_main)
    await mem.delete_all(user_id=proj_feature)
    await mem.delete_all(user_id=proj_other)
    
    # 1. Add unique memories to each branch with random suffixes to avoid deduplication
    import uuid
    suffix = f" {uuid.uuid4().hex[:8]}"
    await mem.add("This is ecommerce codebase on main branch. We are using React and Mantine UI." + suffix, user_id=proj_main, tags=["tech-stack"])
    await mem.add("This is ecommerce codebase on feature-x branch. We added PostgreSQL database support here." + suffix, user_id=proj_feature, tags=["database"])
    await mem.add("This is a completely different project codebase." + suffix, user_id=proj_other, tags=["other"])
    
    # Allow background indexing / vector store to settle if asynchronous
    await asyncio.sleep(0.5)
    
    # 2. Search specific branch 'main'
    hits_main = await mem.search("codebase", user_id=proj_main)
    assert len(hits_main) > 0
    # Every returned hit must belong to the 'main' branch
    for hit in hits_main:
        assert hit["user_id"] == proj_main
        assert "feature-x" not in hit["content"]
        assert "completely different" not in hit["content"]
        
    # 3. Search specific branch 'feature-x'
    hits_feature = await mem.search("codebase", user_id=proj_feature)
    assert len(hits_feature) > 0
    # Every returned hit must belong to the 'feature-x' branch
    for hit in hits_feature:
        assert hit["user_id"] == proj_feature
        assert "React and Mantine UI" not in hit["content"]
        assert "completely different" not in hit["content"]
        
    # 4. Search across all branches of 'test-project-unique-xyz' using a wildcard LIKE pattern
    hits_wildcard = await mem.search("codebase", user_id="test-project-unique-xyz:%")
    assert len(hits_wildcard) >= 2
    
    found_main = False
    found_feature = False
    for hit in hits_wildcard:
        if hit["user_id"] == proj_main:
            found_main = True
        elif hit["user_id"] == proj_feature:
            found_feature = True
        # Verify other-project was not included
        assert hit["user_id"] != proj_other
        
    assert found_main is True
    assert found_feature is True
    
    # 5. Verify stats endpoint wildcard logic
    from projectbrain.server.routes.memory import get_stats
    stats = await get_stats(user_id="test-project-unique-xyz:%")
    assert stats["total_memories"] == 2
    assert "tech-stack" in stats["tags"] or "database" in stats["tags"]
    
    # 6. Verify history wildcard logic
    history_res = mem.history(user_id="test-project-unique-xyz:%", limit=10)
    assert len(history_res) == 2
    
    # Clean up
    await mem.delete_all(user_id=proj_main)
    await mem.delete_all(user_id=proj_feature)
    await mem.delete_all(user_id=proj_other)
