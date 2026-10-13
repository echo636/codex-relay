"""Model output fixtures, never sent to a real model."""
MARKDOWN_SAMPLE = r'''# 公式与 Markdown

行内公式 $E = mc^2$，以及 \(a^2 + b^2 = c^2\)。**重点**、*斜体*和 ~~旧结论~~。

\[
f(x) = \frac{1}{\sigma\sqrt{2\pi}} \exp\left(-\frac{(x-\mu)^2}{2\sigma^2}\right)
\]

## 实验记录

> 引用说明：保留结构，支持中文与公式 $\alpha + \beta$。
>
> 第二段说明。

1. 准备数据
   - 读取图片
   - 归一化
2. 执行实验

- [x] 完成准备
- [ ] 等待下一步

| 模型 | 公式 | 备注 |
| :--- | :---: | ---: |
| 线性模型 | $y = wx+b$ | **可用** |
| 平方误差 | $L = (y-\hat y)^2$ | 123 |

```python
def energy(mass: float) -> float:
    # Formula in code is literal: $E=mc^2$ and \(x\)
    label = "<script>alert(1)</script>"
    return mass * 299792458 ** 2
```

`$not_math$` 和 `\(literal\)` 保留原样。金额 $20 和 $30，转义 \$40。

$$
\begin{aligned}
\nabla_\theta L &= \sum_{i=1}^{n} (\hat y_i-y_i)x_i \\
\theta_{t+1} &= \theta_t - \eta \nabla_\theta L
\end{aligned}
$$

$$\begin{pmatrix}a & b \\ c & d\end{pmatrix}$$

[参考资料](https://example.org/docs) · https://example.org

---

完整渲染结束。
'''
MARKDOWN_PREFIX = MARKDOWN_SAMPLE[:MARKDOWN_SAMPLE.index(r'\exp') + 4]
