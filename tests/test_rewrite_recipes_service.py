import tempfile
import unittest
from pathlib import Path

from services.rewrite_recipes import (
    RecipeApplyError,
    RewriteRecipe,
    RewriteRecipeStore,
    dry_run_recipe,
    guarded_apply_recipe,
)


class TestRewriteRecipesService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openclaw_rewrite_recipe_")
        self.store = RewriteRecipeStore(storage_dir=Path(self.tmp.name))
        self.workflow = {
            "1": {
                "class_type": "KSampler",
                "inputs": {"text": "old", "steps": 20, "width": 512, "height": 512},
            }
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_store_crud(self):
        recipe = RewriteRecipe.new(
            name="prompt swap",
            prompt_template="cinematic {{topic}}",
            tags=["cinematic", "prompt"],
            operations=[{"path": "/1/inputs/text", "value": "{{rewrite_prompt}}"}],
        )
        self.assertTrue(self.store.save_recipe(recipe))

        loaded = self.store.get_recipe(recipe.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, "prompt swap")

        listing = self.store.list_recipes(tag="cinematic")
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0].id, recipe.id)

        self.assertTrue(self.store.delete_recipe(recipe.id))
        self.assertIsNone(self.store.get_recipe(recipe.id))

    def test_dry_run_returns_structured_diff(self):
        recipe = RewriteRecipe.new(
            name="rewrite",
            operations=[{"path": "/1/inputs/text", "value": "new text"}],
        )
        result = dry_run_recipe(recipe, workflow=self.workflow, inputs={})
        self.assertEqual(result["workflow"]["1"]["inputs"]["text"], "new text")
        self.assertGreaterEqual(len(result["diff"]), 1)
        self.assertEqual(result["diff"][0]["change"], "modified")

    def test_s3_clamp_applies_to_common_generation_fields(self):
        recipe = RewriteRecipe.new(
            name="clamp-test",
            operations=[
                {"path": "/1/inputs/steps", "value": "{{steps}}"},
                {"path": "/1/inputs/width", "value": "{{width}}"},
            ],
            constraints={"allowed_inputs": ["steps", "width"]},
        )
        result = dry_run_recipe(
            recipe,
            workflow=self.workflow,
            inputs={"steps": 9999, "width": 1025},
        )
        self.assertEqual(result["workflow"]["1"]["inputs"]["steps"], 100)
        self.assertEqual(result["workflow"]["1"]["inputs"]["width"], 1024)

    def test_guarded_apply_returns_rollback_snapshot_on_failure(self):
        recipe = RewriteRecipe.new(
            name="bad-path",
            operations=[{"path": "/missing/path", "value": "x"}],
        )
        with self.assertRaises(RecipeApplyError) as ctx:
            guarded_apply_recipe(recipe, workflow=self.workflow, inputs={}, confirm=True)
        self.assertEqual(ctx.exception.code, "validation_error")
        self.assertEqual(ctx.exception.rollback_snapshot, self.workflow)


if __name__ == "__main__":
    unittest.main()

