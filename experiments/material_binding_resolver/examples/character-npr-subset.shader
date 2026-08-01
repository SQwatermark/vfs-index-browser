Shader "HGRP/CharacterNPR" {
    Properties {
        _BaseColor ("Base Color", Color) = (1, 1, 1, 1)
        _BaseMap ("Base Map", 2D) = "white" {}
        [Toggle(_METALLICSPECGLOSSMAP)] _UseMetallicGlossMap ("Use Packed Map", Float) = 0
        _Smoothness ("Smoothness", Range(0, 1)) = 0.5
        _MetallicGlossMap ("Metal Spec Shadow Smooth", 2D) = "white" {}
        [Toggle(_NORMALMAP)] _UseBumpMap ("Use Normal Map", Float) = 0
        _BumpMap ("Normal Map", 2D) = "bump" {}
        [Enum(Off, 0, On, 1)] _Mode ("Mode", Int) = 0
        [Toggle(_SILK_STOCKINGS)] _SilkStockings ("Silk Stockings", Float) = 0
        _SilkStockingsDryColor ("Dry Color", Color) = (1, 1, 1, 1)
        _SilkStockingsWetColor ("Wet Color", Color) = (1, 1, 1, 1)
        _SilkStockingsColor ("Edge Color", Color) = (0, 0, 0, 1)
        _SilkStockingsMinAffect ("Min Affect", Range(0, 0.49)) = 0.05
        _SilkStockingsMaxAffect ("Max Affect", Range(0.5, 0.9)) = 0.9
        [ToggleUI] _SilkStockingsAdvance ("Advanced Mask", Float) = 0
        _SilkStockingsAnisoDirection ("Anisotropy Direction", Range(-1, 1)) = 0
        _SilkStockingsMask ("Silk Mask", 2D) = "white" {}
        _SilkStockingsSpecularInt ("Specular Intensity", Float) = 5
        _SilkStockingsSpecularMinAtMinWetness ("Dry Specular Minimum", Range(0, 1)) = 0
        _SilkStockingsSpecularFalloff ("Specular Falloff", Range(0, 1)) = 0.8
        _SilkStockingsSpecularValue ("Specular Offset", Range(-2, 2)) = 2
        _SilkStockingsRainWetMaskScale ("Rain Wet Mask Scale", Range(0, 1)) = 0.7
        _SilkStockingsAlbedoAffectType ("Wet Albedo Mode", Range(-0.9, 0.5)) = 0.5
    }
}
