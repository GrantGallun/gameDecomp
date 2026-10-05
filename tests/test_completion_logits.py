"""The memory-saving path must preserve completion loss and every parameter gradient."""
import pytest


def test_selected_completion_logits_match_qwen_full_masked_loss_and_gradients():
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    from eval import train_source_repair as trainer

    torch.manual_seed(19)
    config = transformers.Qwen2Config(vocab_size=23, hidden_size=16, intermediate_size=32,
                                      num_hidden_layers=1, num_attention_heads=2,
                                      num_key_value_heads=2, max_position_embeddings=32,
                                      attention_dropout=0.0, use_cache=False)
    model = transformers.Qwen2ForCausalLM(config)
    tokens = torch.tensor([[2, 3, 4, 5, 6, 7]])
    labels = torch.tensor([[-100, -100, -100, 5, 6, 7]])
    batch = {"input_ids": tokens, "attention_mask": torch.ones_like(tokens), "labels": labels}
    full = model(**batch).loss
    full.backward()
    gradients = {name: p.grad.clone() for name, p in model.named_parameters() if p.grad is not None}
    model.zero_grad()
    selected = trainer.completion_loss(model, batch)
    torch.testing.assert_close(selected, full)
    selected.backward()
    for name, p in model.named_parameters():
        if name in gradients:
            torch.testing.assert_close(p.grad, gradients[name], atol=1e-6, rtol=1e-5)
