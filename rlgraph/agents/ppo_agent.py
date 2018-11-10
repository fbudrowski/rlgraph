# Copyright 2018 The RLgraph authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as np

from rlgraph.agents import Agent
from rlgraph.components import DictMerger, ContainerSplitter, \
    Memory, PPOLossFunction, ValueFunction, Optimizer
from rlgraph.spaces import BoolBox, FloatBox
from rlgraph.utils.util import strip_list
from rlgraph.utils.decorators import rlgraph_api


class PPOAgent(Agent):
    """
    Proximal policy optimization is a variant of policy optimization in which
    the likelihood ratio between updated and prior policy is constrained by clipping, and
    where updates are performed via repeated sub-sampling of the input batch.

    Paper: https://arxiv.org/abs/1707.06347
    """
    def __init__(self, baseline_spec, clip_ratio=0.2, gae_lambda=1.0, sample_episodes=True, memory_spec=None, **kwargs):
        """
        Args:
            baseline_spec (list): Neural network specification for baseline.
            clip_ratio (float): Clipping parameter for likelihood ratio.
            gae_lambda (float): Lambda for generalized advantage estimation.
            sample_episodes (bool): If true, the update method interprets the batch_size as the number of
                episodes to fetch from the memory. If false, batch_size will refer to the number of time-steps. This
                is especially relevant for environments where episode lengths may vastly differ throughout training. For
                example, in CartPole, a losing episode is typically 10 steps, and a winning episode 200 steps.
            memory_spec (Optional[dict,Memory]): The spec for the Memory to use. Should typically be
            a ring-buffer.
        """
        action_adapter_spec = kwargs.pop("action_adapter_spec", dict(type="action-adapter"))
        super(PPOAgent, self).__init__(
            action_adapter_spec=action_adapter_spec, name=kwargs.pop("name", "actor-critic-agent"), **kwargs
        )
        # Use a stochastic policy.
        self.policy.deterministic_policy = False
        self.sample_episodes = sample_episodes

        # Extend input Space definitions to this Agent's specific API-methods.
        preprocessed_state_space = self.preprocessed_state_space.with_batch_rank()
        reward_space = FloatBox(add_batch_rank=True)
        terminal_space = BoolBox(add_batch_rank=True)

        self.input_spaces.update(dict(
            actions=self.action_space.with_batch_rank(),
            weights="variables:policy",
            time_step=int,
            use_exploration=bool,
            preprocessed_states=preprocessed_state_space,
            rewards=reward_space,
            terminals=terminal_space
        ))

        # The merger to merge inputs into one record Dict going into the memory.
        self.merger = DictMerger("states", "actions", "rewards", "terminals")
        assert memory_spec["type"] == "ring_buffer", "PPO memory must be ring-buffer for episode-handling."
        self.memory = Memory.from_spec(memory_spec)
        # The splitter for splitting up the records coming from the memory.
        self.splitter = ContainerSplitter("states", "actions", "rewards", "terminals")

        self.loss_function = PPOLossFunction(discount=self.discount, gae_lambda=gae_lambda, clip_ratio=clip_ratio)

        # TODO make network sharing optional.
        # Create non-shared baseline network.
        self.value_function = ValueFunction(network_spec=baseline_spec)
        self.value_function_optimizer = Optimizer.from_spec(self.optimizer_spec)

        # Add all our sub-components to the core.
        sub_components = [self.preprocessor, self.merger, self.memory, self.splitter, self.policy,
                          self.exploration, self.loss_function, self.optimizer,
                          self.value_function, self.value_function_optimizer]
        self.root_component.add_components(*sub_components)

        # Define the Agent's (root-Component's) API.
        self.define_graph_api("value_function", "policy", "preprocessor-stack", self.optimizer.scope, *sub_components)

        if self.auto_build:
            self._build_graph([self.root_component], self.input_spaces, optimizer=self.optimizer,
                              batch_size=self.update_spec["batch_size"])
            self.graph_built = True

    def define_graph_api(self, value_function_scope, policy_scope, pre_processor_scope, optimizer_scope, *sub_components):
        super(PPOAgent, self).define_graph_api(policy_scope, pre_processor_scope)

        preprocessor, merger, memory, splitter, policy, exploration, loss_function, optimizer, value_function, vf_opt\
            = sub_components
        sample_episodes = self.sample_episodes

        # Reset operation (resets preprocessor).
        if self.preprocessing_required:
            @rlgraph_api(component=self.root_component)
            def reset_preprocessor(self):
                reset_op = preprocessor.reset()
                return reset_op

        # Act from preprocessed states.
        @rlgraph_api(component=self.root_component)
        def action_from_preprocessed_state(self, preprocessed_states, use_exploration=True):
            out = policy.get_action(preprocessed_states, use_exploration)
            return preprocessed_states, out["action"]

        # State (from environment) to action with preprocessing.
        @rlgraph_api(component=self.root_component)
        def get_preprocessed_state_and_action(self, states, use_exploration=True):
            preprocessed_states = preprocessor.preprocess(states)
            return self.action_from_preprocessed_state(preprocessed_states, use_exploration)

        # Insert into memory.
        @rlgraph_api(component=self.root_component)
        def insert_records(self, preprocessed_states, actions, rewards, terminals):
            records = merger.merge(preprocessed_states, actions, rewards, terminals)
            return memory.insert_records(records)

        # Learn from memory.
        @rlgraph_api(component=self.root_component)
        def update_from_memory(self_):
            if sample_episodes:
                records = memory.get_episodes(self.update_spec["batch_size"])
            else:
                records = memory.get_records(self.update_spec["batch_size"])
            preprocessed_s, actions, rewards, terminals = splitter.split(records)

            # TODO subsample here
            step_op, loss, loss_per_item = self_.update_from_external_batch(
                preprocessed_s, actions, rewards, terminals
            )

            return step_op, loss, loss_per_item, records

        # Learn from an external batch.
        @rlgraph_api(component=self.root_component)
        def update_from_external_batch(
                self_, preprocessed_states, actions, rewards, terminals
        ):
            # If we are a multi-GPU root:
            # Simply feeds everything into the multi-GPU sync optimizer's method and return.
            if "multi-gpu-sync-optimizer" in self_.sub_components:
                main_policy_vars = self_.get_sub_component_by_name(policy_scope)._variables()
                grads_and_vars, loss, loss_per_item = \
                    self_.sub_components["multi-gpu-sync-optimizer"].calculate_update_from_external_batch(
                        main_policy_vars, preprocessed_states, actions, rewards, terminals
                    )
                step_op = self_.get_sub_component_by_name(optimizer_scope).apply_gradients(grads_and_vars)
                step_and_sync_op = self_.sub_components["multi-gpu-sync-optimizer"].sync_policy_weights_to_towers(
                    step_op, main_policy_vars
                )
                return step_and_sync_op, loss, loss_per_item

            action_log_probs,  = policy.get_logits_probabilities_log_probs(preprocessed_states)
            baseline_values = value_function.value_output(preprocessed_states)

            # log_probs, baseline_values, actions, rewards, terminals, prev_log_probs
            loss, loss_per_item, v_loss, v_loss_per_item = self_.get_sub_component_by_name(loss_function.scope).loss(
                action_log_probs, baseline_values, actions, rewards, terminals
            )

            # Args are passed in again because some device strategies may want to split them to different devices.
            policy_vars = self_.get_sub_component_by_name(policy_scope)._variables()

            if hasattr(self_, "is_multi_gpu_tower") and self_.is_multi_gpu_tower is True:
                grads_and_vars = self_.get_sub_component_by_name(optimizer_scope).calculate_gradients(policy_vars, loss)
                return grads_and_vars, loss, loss_per_item
            else:
                step_op, loss, loss_per_item = optimizer.step(policy_vars, loss, loss_per_item)
                return step_op, loss, loss_per_item

    def get_action(self, states, internals=None, use_exploration=True, apply_preprocessing=True, extra_returns=None):
        """
        Args:
            extra_returns (Optional[Set[str],str]): Optional string or set of strings for additional return
                values (besides the actions). Possible values are:
                - 'preprocessed_states': The preprocessed states after passing the given states through the
                preprocessor stack.
                - 'internal_states': The internal states returned by the RNNs in the NN pipeline.
                - 'used_exploration': Whether epsilon- or noise-based exploration was used or not.

        Returns:
            tuple or single value depending on `extra_returns`:
                - action
                - the preprocessed states
        """
        extra_returns = {extra_returns} if isinstance(extra_returns, str) else (extra_returns or set())
        # States come in without preprocessing -> use state space.
        if apply_preprocessing:
            call_method = "get_preprocessed_state_and_action"
            batched_states = self.state_space.force_batch(states)
        else:
            call_method = "action_from_preprocessed_state"
            batched_states = states
        remove_batch_rank = batched_states.ndim == np.asarray(states).ndim + 1

        # Increase timesteps by the batch size (number of states in batch).
        batch_size = len(batched_states)
        self.timesteps += batch_size

        # Control, which return value to "pull" (depending on `additional_returns`).
        return_ops = [1, 0] if "preprocessed_states" in extra_returns else [1]
        ret = self.graph_executor.execute((
            call_method,
            [batched_states, self.timesteps, use_exploration],
            # 0=preprocessed_states, 1=action
            return_ops
        ))
        if remove_batch_rank:
            return strip_list(ret)
        else:
            return ret

    # TODO make next states optional in observe API.
    def _observe_graph(self, preprocessed_states, actions, internals, rewards, next_states, terminals):
        self.graph_executor.execute(("insert_records", [preprocessed_states, actions, rewards, terminals]))

    def update(self, batch=None):

        # [0]=no-op step; [1]=the loss; [2]=loss-per-item, [3]=memory-batch (if pulled)
        return_ops = [0, 1, 2]
        if batch is None:
            ret = self.graph_executor.execute(("update_from_memory", None, return_ops))

            # Remove unnecessary return dicts (e.g. sync-op).
            if isinstance(ret, dict):
                ret = ret["update_from_memory"]
        else:
            batch_input = [batch["states"], batch["actions"], batch["rewards"], batch["terminals"]]
            ret = self.graph_executor.execute(("update_from_external_batch", batch_input, return_ops))

            # Remove unnecessary return dicts (e.g. sync-op).
            if isinstance(ret, dict):
                ret = ret["update_from_external_batch"]

        # [1]=the loss (0=update noop)
        # [2]=loss per item for external update, records for update from memory
        return ret[1], ret[2]

    def reset(self):
        """
        Resets our preprocessor, but only if it contains stateful PreprocessLayer Components (meaning
        the PreprocessorStack has at least one variable defined).
        """
        if self.preprocessing_required and len(self.preprocessor.variables) > 0:
            self.graph_executor.execute("reset_preprocessor")

    def __repr__(self):
        return "PPOAgent"
