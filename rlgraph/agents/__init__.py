# Copyright 2018/2019 The RLgraph authors. All Rights Reserved.
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

from __future__ import absolute_import, division, print_function

from rlgraph.agents.agent import Agent
from rlgraph.agents.actor_critic_agent import ActorCriticAgent
from rlgraph.agents.apex_agent import ApexAgent
from rlgraph.agents.dqfd_agent import DQFDAgent
from rlgraph.agents.dqn_agent import DQNAgent, DQNAlgorithmComponent
from rlgraph.agents.impala_agents import OldBaseAgent, IMPALAAgent, SingleIMPALAAgent
from rlgraph.agents.ppo_agent import PPOAgent, PPOAlgorithmComponent
from rlgraph.agents.random_agent import RandomAgent
from rlgraph.agents.sac_agent import SACAgent
from rlgraph.components.algorithms.algorithm_component import AlgorithmComponent

Agent.__lookup_classes__ = dict(
    apex=ApexAgent,
    apexagent=ApexAgent,
    actorcritic=ActorCriticAgent,
    dqn=DQNAgent,
    dqnagent=DQNAgent,
    dqfd=DQFDAgent,
    dqfdagent=DQFDAgent,
    ppo=PPOAgent,
    ppoagent=PPOAgent,
    random=RandomAgent,
    randomagent=RandomAgent,
    sac=SACAgent,
    sacagent=SACAgent
)

OldBaseAgent.__lookup_classes__ = dict(
    impala=IMPALAAgent,
    singleimpala=SingleIMPALAAgent,
    singleimpalaagent=SingleIMPALAAgent
)

AlgorithmComponent.__lookup_classes__ = dict(
    #dqfdlgorithmcomponent=DQFDAlgorithmComponent,
    dqnalgorithmcomponent=DQNAlgorithmComponent,
    ppoalgorithmcomponent=PPOAlgorithmComponent,
    #sacalgorithmcomponent=SACAlgorithmComponent
)
AlgorithmComponent.__default_constructor__ = PPOAlgorithmComponent


__all__ = ["Agent"] + \
          list(set(map(lambda x: x.__name__, Agent.__lookup_classes__.values()))) + \
          list(set(map(lambda x: x.__name__, OldBaseAgent.__lookup_classes__.values()))) + \
          list(set(map(lambda x: x.__name__, list(AlgorithmComponent.__lookup_classes__.values()))))

