import * as THREE from 'three'

const MODEL_ANIMATION_FORMAT = 'EndfieldModelAnimation'
const MODEL_ANIMATION_VERSION = '1.0.0'
const propertyNames = {
  translation: 'position',
  rotation: 'quaternion',
  scale: 'scale',
}

export function createModelAnimationClip(animation, root) {
  if (
    animation?.format !== MODEL_ANIMATION_FORMAT
    || animation?.version !== MODEL_ANIMATION_VERSION
    || !Array.isArray(animation.timelines)
    || !Array.isArray(animation.tracks)
  ) {
    throw new Error('不支持的模型动画数据')
  }

  const targets = new Map()
  root.traverse((object) => {
    const nodeId = object.userData?.endfieldNodeId
    if (!nodeId) return
    if (targets.has(nodeId)) {
      throw new Error(`模型包含重复的节点标识：${nodeId}`)
    }
    targets.set(nodeId, object)
  })

  const tracks = animation.tracks.map((track) => {
    const target = targets.get(track.targetId)
    if (!target) {
      throw new Error(`动画目标节点不存在：${track.targetId}`)
    }
    const times = animation.timelines[track.timeline]
    const propertyName = propertyNames[track.property]
    if (!Array.isArray(times) || !propertyName || !Array.isArray(track.values)) {
      throw new Error(`动画轨道格式无效：${track.targetId}`)
    }
    const TrackType = track.property === 'rotation'
      ? THREE.QuaternionKeyframeTrack
      : THREE.VectorKeyframeTrack
    return new TrackType(
      `${target.uuid}.${propertyName}`,
      times,
      track.values.flat(),
    )
  })
  const duration = Number(animation.duration)
  return new THREE.AnimationClip(
    animation.name || 'AnimationClip',
    Number.isFinite(duration) && duration >= 0 ? duration : -1,
    tracks,
  )
}
