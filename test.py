import gym

from vizdoom import gym_wrapper  # noqa


if __name__ == "__main__":
    env = gym.make("VizdoomCvPathfindersBlue-v0", render_mode="human")
    print(env.observation_space)
    # Rendering random rollouts for ten episodes
    for _ in range(10):
        done = False
        obs = env.reset()
        while not done:
            action = env.action_space.sample()
            print(action)
            obs, rew, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            env.render()